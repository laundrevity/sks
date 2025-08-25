<script lang="ts">
	import { onMount, tick } from 'svelte';
	import {
		createConversation,
		getConversation,
		streamChatInConversation
	} from '$lib/api';
	import type {
		DeltaKind,
		DeltaPayload,
		Msg,
		Part,
		FuncPart,
		CustomPart
	} from '$lib/types';

	let convId = $state<string | null>(null);

	let messages = $state<Msg[]>([
		{
			id: crypto.randomUUID(),
			role: 'assistant',
			parts: [
				{ type: 'text', text: "👋 Hey! I’m ready when you are." },
				{ type: 'reasoning', text: 'Tip: Shift+Enter for newline' }
			]
		}
	]);

	let input = $state('');
	let isStreaming = $state(false);
	let lastTokens: number | null = $state(null);

	// Track per-item bubbles using reactive records
	let reasoningMsgByItemId = $state<Record<string, string>>({});
	let textMsgByItemId = $state<Record<string, string>>({});
	let funcMsgByItemId = $state<Record<string, string>>({});
	let custMsgByItemId = $state<Record<string, string>>({});

	let controller: AbortController | null = null;

	// ---- conversation bootstrap ----
	async function ensureConversation() {
		// Try localStorage
		const existing = typeof window !== 'undefined' ? localStorage.getItem('convId') : null;
		if (existing) {
			// Verify it still exists server-side (DB might have been reset)
			const ok = await getConversation(existing).catch(() => null);
			if (ok) {
				convId = existing;
				return;
			}
		}
		// Create new
		const id = await createConversation().catch(() => null);
		if (!id) throw new Error('Failed to create conversation');
		convId = id;
		if (typeof window !== 'undefined') localStorage.setItem('convId', id);
	}

	onMount(async () => {
		try {
			await ensureConversation();
		} catch (e) {
			// Surface a tiny bubble error; user can refresh
			messages.push({
				id: crypto.randomUUID(),
				role: 'assistant',
				parts: [{ type: 'text', text: '⚠️ Could not initialize conversation.' }]
			});
		}
	});

	function newMsg(parts: Part[]): Msg {
		const m: Msg = { id: crypto.randomUUID(), role: 'assistant', parts: [...parts] };
		messages.push(m);
		return m;
	}

	function appendText(msg: Msg, kind: 'text' | 'reasoning', chunk: string) {
		let idx = msg.parts.findIndex((p) => p.type === kind);
		if (idx === -1) {
			const newPart: Part =
				kind === 'text' ? { type: 'text', text: '' } : { type: 'reasoning', text: '' };
			msg.parts.push(newPart);
			idx = msg.parts.length - 1;
		}
		const part = msg.parts[idx] as Extract<Part, { type: 'text' | 'reasoning' }>;
		part.text += chunk;
	}

	function startToolBubble(
		kind: 'function' | 'custom',
		name: string,
		call_id: string,
		item_id: string
	) {
		const part: FuncPart | CustomPart =
			kind === 'function'
				? { type: 'function', name, call_id, text: '' }
				: { type: 'custom', name, call_id, text: '' };
		const msg = newMsg([part]);
		if (kind === 'function') funcMsgByItemId[item_id] = msg.id;
		else custMsgByItemId[item_id] = msg.id;
	}

	function handleDelta(kind: DeltaKind, d: DeltaPayload) {
		switch (kind) {
			case 'item.started': {
				const t = (d.meta?.['type'] as string | undefined) ?? undefined;
				const itemId = d.item_id ?? '';
				if (!itemId) break;

				if (t === 'reasoning') {
					const m = newMsg([{ type: 'reasoning', text: '' }]);
					reasoningMsgByItemId[itemId] = m.id;
				} else if (t === 'function_call') {
					startToolBubble('function', d.name ?? 'unknown', d.call_id ?? '', itemId);
				} else if (t === 'custom_tool_call') {
					startToolBubble('custom', d.name ?? 'unknown', d.call_id ?? '', itemId);
				} else if (t === 'message') {
					const m = newMsg([{ type: 'text', text: '' }]);
					textMsgByItemId[itemId] = m.id;
				}
				break;
			}

			case 'reasoning': {
				const itemId = d.item_id ?? '';
				if (!itemId) break;
				const msgId = reasoningMsgByItemId[itemId];
				let msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (!msg) {
					msg = newMsg([{ type: 'reasoning', text: '' }]);
					reasoningMsgByItemId[itemId] = msg.id;
				}
				appendText(msg, 'reasoning', d.text ?? '');
				break;
			}

			case 'text': {
				const itemId = d.item_id ?? '';
				if (!itemId) break;
				const msgId = textMsgByItemId[itemId];
				let msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (!msg) {
					msg = newMsg([{ type: 'text', text: '' }]);
					textMsgByItemId[itemId] = msg.id;
				}
				appendText(msg, 'text', d.text ?? '');
				break;
			}

			case 'function.arguments': {
				const itemId = d.item_id ?? '';
				if (!itemId) break;
				const msgId = funcMsgByItemId[itemId];
				const msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (msg && msg.parts[0]?.type === 'function') {
					(msg.parts[0] as FuncPart).text += d.text ?? '';
				}
				break;
			}

			case 'custom.input': {
				const itemId = d.item_id ?? '';
				if (!itemId) break;
				const msgId = custMsgByItemId[itemId];
				const msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (msg && msg.parts[0]?.type === 'custom') {
					(msg.parts[0] as CustomPart).text += d.text ?? '';
				}
				break;
			}

			case 'response.status': {
				if (d.status === 'completed') {
					const usage = d.meta?.['usage'] as { total_tokens?: number } | undefined;
					if (usage?.total_tokens !== undefined) lastTokens = usage.total_tokens;
				}
				break;
			}

			case 'response.usage': {
				const tt = (d as unknown as { total_tokens?: number }).total_tokens;
				if (typeof tt === 'number') lastTokens = tt;
				break;
			}

			case 'error': {
				newMsg([{ type: 'text', text: '⚠️ Error while streaming. Please try again.' }]);
				break;
			}
			default:
				break;
		}
	}

	// --- improved auto-scroll: only if near bottom ---
	let endRef: HTMLDivElement | null = null;
	let scroller: HTMLDivElement | null = null;
	let keepAutoscroll = true;

	function nearBottom(el: HTMLElement, px = 80) {
		return el.scrollHeight - el.scrollTop - el.clientHeight < px;
	}

	function scrollToBottom() {
		if (!scroller) return;
		if (!keepAutoscroll) return;
		scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
	}

	function onScroll() {
		if (!scroller) return;
		keepAutoscroll = nearBottom(scroller);
	}

	async function streamOnce(prompt: string) {
		if (!convId) {
			newMsg([{ type: 'text', text: '⚠️ No conversation. Refresh the page.' }]);
			return;
		}

		controller?.abort();
		controller = new AbortController();
		isStreaming = true;
		lastTokens = null;
		keepAutoscroll = true;

		try {
			await streamChatInConversation(convId, prompt, controller.signal, (msg) => {
				handleDelta(msg.event as DeltaKind, msg.data as DeltaPayload);
				// scroll after each delta, if user hasn’t scrolled up
				scrollToBottom();
			});
		} finally {
			isStreaming = false;
			reasoningMsgByItemId = {};
			textMsgByItemId = {};
			funcMsgByItemId = {};
			custMsgByItemId = {};
			await tick();
			scrollToBottom();
		}
	}

	const onSubmit = async (e: SubmitEvent) => {
		e.preventDefault();
		const prompt = input.trim();
		if (!prompt) return;

		messages.push({
			id: crypto.randomUUID(),
			role: 'user',
			parts: [{ type: 'text', text: prompt }]
		});

		input = '';
		await tick();
		scrollToBottom();

		try {
			await streamOnce(prompt);
		} catch {
			newMsg([{ type: 'text', text: '⚠️ Request failed. Check the API server.' }]);
			isStreaming = false;
		}
	};

	// keep scroll pinned during normal non-stream updates when user is at bottom
	$effect(() => {
		void messages.length;
		queueMicrotask(scrollToBottom);
	});
</script>

<div class="min-h-dvh grid grid-rows-[1fr_auto]">
	<!-- Messages -->
	<div class="overflow-y-auto [scroll-padding-bottom:7rem]" bind:this={scroller} onscroll={onScroll}>
		<div class="mx-auto max-w-3xl px-4 py-6 space-y-3 pb-32">
			{#each messages as m (m.id)}
				{#if m.role === 'user'}
					<div class="flex justify-end">
						<div class="bubble-user">
							{#each m.parts as p, i (m.id + ':user' + i)}
								{#if p.type === 'text'}
									<div class="whitespace-pre-wrap leading-relaxed">{p.text}</div>
								{/if}
							{/each}
						</div>
					</div>
				{:else}
					{#if m.parts.length === 1 && (m.parts[0].type === 'function' || m.parts[0].type === 'custom')}
						<!-- Tool call bubble -->
						<div class="flex justify-start">
							{#if m.parts[0].type === 'function'}
								{@const fp = m.parts[0] as FuncPart}
								<div class="bubble bg-cyan-950 border-cyan-800">
									<div class="text-xs uppercase tracking-wide text-cyan-300/90">
										function_call · {fp.call_id} · {fp.name}
									</div>
									<pre class="mt-1 text-sm whitespace-pre-wrap">{fp.text}</pre>
								</div>
							{:else}
								{@const cp = m.parts[0] as CustomPart}
								<div class="bubble bg-fuchsia-950 border-fuchsia-800">
									<div class="text-xs uppercase tracking-wide text-fuchsia-300/90">
										custom_tool_call · {cp.call_id} · {cp.name}
									</div>
									<pre class="mt-1 text-sm whitespace-pre-wrap">{cp.text}</pre>
								</div>
							{/if}
						</div>
					{:else}
						<!-- Reasoning + Answer bubbles (assistant) -->
						<div class="flex justify-start">
							<div class="bubble-asst">
								{#each m.parts as p, i (m.id + ':asst' + i)}
									{#if p.type === 'text'}
										<div class="whitespace-pre-wrap leading-relaxed">{p.text}</div>
									{:else if p.type === 'reasoning'}
										<div class="mt-1 text-sm italic text-zinc-400">{p.text}</div>
									{/if}
								{/each}
							</div>
						</div>
					{/if}
				{/if}
			{/each}

			{#if isStreaming}
				<div class="flex justify-start">
					<div class="bubble-asst">
						<span class="inline-flex gap-1 align-middle">
							<span class="size-2 animate-bounce rounded-full bg-zinc-400"></span>
							<span class="size-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:120ms]"></span>
							<span class="size-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:240ms]"></span>
						</span>
					</div>
				</div>
			{/if}

			<div class="h-24"></div>
		</div>
	</div>

	<!-- Composer -->
	<form onsubmit={onSubmit} class="sticky bottom-0 bg-zinc-950/80 backdrop-blur supports-[backdrop-filter]:bg-zinc-950/60 border-t border-zinc-800">
		<div class="mx-auto max-w-3xl px-4 py-3">
			<div class="flex items-end gap-3">
				<textarea
					bind:value={input}
					name="prompt"
					placeholder={convId ? "Message the model…" : "Initializing conversation…"}
					disabled={!convId}
					rows="1"
					onkeydown={(e) => {
						if (e.key === 'Enter' && !e.shiftKey) {
							e.preventDefault();
							(e.currentTarget.form as HTMLFormElement)?.requestSubmit();
						}
					}}
					oninput={(e) => {
						const el = e.currentTarget as HTMLTextAreaElement;
						el.style.height = '0px';
						el.style.height = Math.min(el.scrollHeight, 180) + 'px';
					}}
					class="min-h-[44px] max-h-44 w-full resize-none rounded-xl border border-zinc-800 bg-zinc-900/80 px-3 py-2 text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-sky-600/50"
				></textarea>
				<button
					type="submit"
					title="Send"
					disabled={!input.trim() || isStreaming || !convId}
					class="shrink-0 rounded-xl bg-sky-600 px-4 py-2.5 font-medium text-white hover:bg-sky-500 disabled:opacity-60"
				>
					Send
				</button>
			</div>

			<div class="mt-2 text-xs text-zinc-500">
				{#if convId}
					Conversation: <span class="font-mono">{convId.slice(0,8)}…</span> · Total tokens: {lastTokens ?? '—'}
				{:else}
					Setting up conversation…
				{/if}
			</div>
		</div>
	</form>
</div>

<style>
	@keyframes bounce {
		0%, 100% { transform: translateY(0); }
		50% { transform: translateY(-20%); }
	}
	.animate-bounce { animation: bounce 1s infinite ease-in-out; }
</style>

