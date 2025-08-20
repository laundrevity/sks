<script lang="ts">
	type Role = 'user' | 'assistant';

	type TextPart = { type: 'text'; text: string };
	type ReasoningPart = { type: 'reasoning'; text: string };
	type FuncPart = { type: 'function'; name: string; call_id: string; text: string };
	type CustomPart = { type: 'custom'; name: string; call_id: string; text: string };

	type Part = TextPart | ReasoningPart | FuncPart | CustomPart;
	type Msg = { id: string; role: Role; parts: Part[] };

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

	// Track per-item bubbles so we can append correctly and preserve order.
	const reasoningMsgByItemId = new Map<string, string>();
	const textMsgByItemId = new Map<string, string>();
	const funcMsgByItemId = new Map<string, string>();
	const custMsgByItemId = new Map<string, string>();

	let controller: AbortController | null = null;
	const API_URL = 'http://localhost:8000/v1/stream';

	function newMsg(parts: Part[]): Msg {
		const m: Msg = { id: crypto.randomUUID(), role: 'assistant', parts: [...parts] };
		messages.push(m);
		return m;
	}

	function appendText(msg: Msg, kind: 'text' | 'reasoning', chunk: string) {
		let part = [...msg.parts].reverse().find((p) => p.type === kind) as TextPart | ReasoningPart | undefined;
		if (!part) {
			part = (kind === 'text'
				? { type: 'text', text: '' }
				: { type: 'reasoning', text: '' }) as any;
			msg.parts.push(part);
		}
		part.text += chunk;
	}

	function startToolBubble(kind: 'function' | 'custom', name: string, call_id: string, item_id: string) {
		const msg =
			kind === 'function'
				? newMsg([{ type: 'function', name, call_id, text: '' }])
				: newMsg([{ type: 'custom', name, call_id, text: '' }]);

		if (kind === 'function') funcMsgByItemId.set(item_id, msg.id);
		else custMsgByItemId.set(item_id, msg.id);
	}

	async function streamOnce(prompt: string) {
		controller?.abort();
		controller = new AbortController();
		isStreaming = true;
		lastTokens = null;

		const res = await fetch(API_URL, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ prompt, session: 'default' }),
			signal: controller.signal
		});

		if (!res.ok || !res.body) {
			isStreaming = false;
			throw new Error(`HTTP ${res.status}`);
		}

		const reader = res.body.getReader();
		const decoder = new TextDecoder('utf-8');
		let buf = '';

		const flushEvent = (raw: string) => {
			const lines = raw.split('\n').map((l) => l.trimEnd());
			let ev = 'message';
			const dataLines: string[] = [];
			for (const line of lines) {
				if (line.startsWith('event:')) ev = line.slice(6).trim();
				else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
			}
			if (!dataLines.length) return;

			let payload: any;
			try {
				payload = JSON.parse(dataLines.join('\n'));
			} catch {
				return;
			}
			handleDelta(ev, payload);
		};

		try {
			while (true) {
				const { done, value } = await reader.read();
				if (done) break;
				buf += decoder.decode(value, { stream: true });
				let idx: number;
				while ((idx = buf.indexOf('\n\n')) !== -1) {
					const chunk = buf.slice(0, idx);
					buf = buf.slice(idx + 2);
					if (chunk.trim()) flushEvent(chunk);
				}
			}
		} finally {
			if (buf.trim()) flushEvent(buf);
			isStreaming = false;

			// Clear per-item maps for the next prompt/response round
			reasoningMsgByItemId.clear();
			textMsgByItemId.clear();
			funcMsgByItemId.clear();
			custMsgByItemId.clear();
		}
	}

	function handleDelta(kind: string, d: any) {
		switch (kind) {
			case 'item.started': {
				const t = d?.meta?.type as string | undefined;
				const itemId = d?.item_id ?? '';
				if (t === 'reasoning') {
					// Create a dedicated reasoning bubble
					const m = newMsg([{ type: 'reasoning', text: '' }]);
					reasoningMsgByItemId.set(itemId, m.id);
				} else if (t === 'function_call') {
					startToolBubble('function', d?.name ?? 'unknown', d?.call_id ?? '', itemId);
				} else if (t === 'custom_tool_call') {
					startToolBubble('custom', d?.name ?? 'unknown', d?.call_id ?? '', itemId);
				} else if (t === 'message') {
					// Create a dedicated text bubble for the final assistant answer
					const m = newMsg([{ type: 'text', text: '' }]);
					textMsgByItemId.set(itemId, m.id);
				}
				break;
			}

			case 'reasoning': {
				const itemId = d?.item_id ?? '';
				const msgId = reasoningMsgByItemId.get(itemId);
				let msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (!msg) {
					// If for some reason 'reasoning' delta precedes item.started
					msg = newMsg([{ type: 'reasoning', text: '' }]);
					reasoningMsgByItemId.set(itemId, msg.id);
				}
				appendText(msg, 'reasoning', d?.text ?? '');
				break;
			}

			case 'text': {
				const itemId = d?.item_id ?? '';
				const msgId = textMsgByItemId.get(itemId);
				let msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (!msg) {
					// If for some reason text arrives before item.started
					msg = newMsg([{ type: 'text', text: '' }]);
					textMsgByItemId.set(itemId, msg.id);
				}
				appendText(msg, 'text', d?.text ?? '');
				break;
			}

			case 'function.arguments': {
				const itemId = d?.item_id ?? '';
				const msgId = funcMsgByItemId.get(itemId);
				const msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (msg && msg.parts[0]?.type === 'function') {
					(msg.parts[0] as FuncPart).text += d?.text ?? '';
				}
				break;
			}

			case 'custom.input': {
				const itemId = d?.item_id ?? '';
				const msgId = custMsgByItemId.get(itemId);
				const msg = msgId ? messages.find((m) => m.id === msgId) : undefined;
				if (msg && msg.parts[0]?.type === 'custom') {
					(msg.parts[0] as CustomPart).text += d?.text ?? '';
				}
				break;
			}

			case 'response.status': {
				if (d?.status === 'completed') {
					const tt = d?.meta?.usage?.total_tokens;
					if (typeof tt === 'number') lastTokens = tt;
				}
				break;
			}

			case 'error': {
				newMsg([{ type: 'text', text: '⚠️ Error while streaming. Please try again.' }]);
				break;
			}
		}
	}

	const onSubmit = async (e: SubmitEvent) => {
		e.preventDefault();
		const prompt = input.trim();
		if (!prompt) return;

		// user bubble
		messages.push({
			id: crypto.randomUUID(),
			role: 'user',
			parts: [{ type: 'text', text: prompt }]
		});

		input = '';

		try {
			await streamOnce(prompt);
		} catch (err) {
			newMsg([{ type: 'text', text: '⚠️ Request failed. Check the API server.' }]);
			isStreaming = false;
		}
	};

	// auto-scroll
	let endRef: HTMLDivElement | null = null;
	$effect(() => {
		void messages.length;
		queueMicrotask(() => endRef?.scrollIntoView({ behavior: 'smooth', block: 'end' }));
	});
</script>

<div class="min-h-dvh grid grid-rows-[1fr_auto]">
	<!-- Messages -->
	<div class="overflow-y-auto">
		<div class="mx-auto max-w-3xl px-4 py-6 space-y-3">
			{#each messages as m (m.id)}
				{#if m.role === 'user'}
					<div class="flex justify-end">
						<div class="bubble-user">
							{#each m.parts as p}
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
								<div class="bubble bg-cyan-950 border-cyan-800">
									<div class="text-xs uppercase tracking-wide text-cyan-300/90">
										function_call · {(m.parts[0] as any).call_id} · [{(m.parts[0] as any).name}]
									</div>
									<pre class="mt-1 text-sm whitespace-pre-wrap">{(m.parts[0] as any).text}</pre>
								</div>
							{:else}
								<div class="bubble bg-fuchsia-950 border-fuchsia-800">
									<div class="text-xs uppercase tracking-wide text-fuchsia-300/90">
										custom_tool_call · {(m.parts[0] as any).call_id} · [{(m.parts[0] as any).name}]
									</div>
									<pre class="mt-1 text-sm whitespace-pre-wrap">{(m.parts[0] as any).text}</pre>
								</div>
							{/if}
						</div>
					{:else}
						<!-- Reasoning + Answer bubbles (assistant) -->
						<div class="flex justify-start">
							<div class="bubble-asst">
								{#each m.parts as p}
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

			<div bind:this={endRef}></div>
		</div>
	</div>

	<!-- Composer -->
	<form onsubmit={onSubmit} class="sticky bottom-0 bg-zinc-950/80 backdrop-blur supports-[backdrop-filter]:bg-zinc-950/60 border-t border-zinc-800">
		<div class="mx-auto max-w-3xl px-4 py-3">
			<div class="flex items-end gap-3">
				<textarea
					bind:value={input}
					name="prompt"
					placeholder="Message the model…"
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
				/>
				<button
					type="submit"
					title="Send"
					disabled={!input.trim() || isStreaming}
					class="shrink-0 rounded-xl bg-sky-600 px-4 py-2.5 font-medium text-white hover:bg-sky-500 disabled:opacity-60"
				>
					Send
				</button>
			</div>

			<div class="mt-2 text-xs text-zinc-500">
				Total tokens: {lastTokens ?? '—'}
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

