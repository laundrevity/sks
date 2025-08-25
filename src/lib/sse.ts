export type SSEMessage<T = unknown> = { event: string; data: T };

export async function* readSSE(
    stream: ReadableStream<Uint8Array>, 
    decoder = new TextDecoder() 
): AsyncGenerator<SSEMessage> {
    const reader = stream.getReader();
    let buf = "";
    let event = "message";
    let dataLines: string[] = [];

    const flush = (): SSEMessage | null => {
        if (dataLines.length === 0) return null;
        const dataStr = dataLines.join("\n");
        let data: unknown = dataStr;
        try { data = JSON.parse(dataStr); } catch { /* plain text */ }
        const msg = { event, data };
        // reset
        event = "message";
        dataLines = [];
        return msg;
    };

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        while (true) {
            const nl = buf.indexOf("\n");
            if (nl === -1) break;
            const line = buf.slice(0, nl);
            buf = buf.slice(nl + 1);

            if (line === "") {
                const msg = flush();
                if (msg) yield msg;
                continue;
            }
            if (line.startsWith(":")) {
                // comment / heartbeat
                continue;
            }
            if (line.startsWith("event:")) {
                event = line.slice(6).trim() || "message";
                continue;
            }
            if (line.startsWith("data:")) {
                dataLines.push(line.slice(5).trimStart());
                continue;
            }
            // other fields (id, retry) ignored for now
        }
    }
    const msg = flush();
    if (msg) yield msg;
}

