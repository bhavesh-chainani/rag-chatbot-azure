import { Answers, IHistoryProvider } from "./components/HistoryProviders/IProvider";

type SaveJob = {
    answers: Answers;
    getToken: () => Promise<string | undefined>;
};

type SessionQueue = {
    pending: SaveJob | null;
    processing: boolean;
    processPromise: Promise<void> | null;
};

const queues = new Map<string, SessionQueue>();

function getQueue(sessionId: string): SessionQueue {
    let queue = queues.get(sessionId);
    if (!queue) {
        queue = { pending: null, processing: false, processPromise: null };
        queues.set(sessionId, queue);
    }
    return queue;
}

async function drainQueue(sessionId: string, historyManager: IHistoryProvider): Promise<void> {
    const queue = getQueue(sessionId);
    try {
        while (queue.pending) {
            const job = queue.pending;
            queue.pending = null;
            try {
                const token = await job.getToken();
                await historyManager.addItem(sessionId, job.answers, token);
            } catch (e) {
                console.error("Failed to save chat to history:", e);
            }
        }
    } finally {
        queue.processing = false;
        queue.processPromise = null;
        if (queue.pending) {
            startDrain(sessionId, historyManager);
        }
    }
}

function startDrain(sessionId: string, historyManager: IHistoryProvider): void {
    const queue = getQueue(sessionId);
    if (queue.processing) {
        return;
    }
    queue.processing = true;
    queue.processPromise = drainQueue(sessionId, historyManager);
}

/** Coalesce rapid saves per session; only the latest snapshot is sent once prior work finishes. */
export function enqueueHistorySave(
    sessionId: string,
    answers: Answers,
    getTokenFn: () => Promise<string | undefined>,
    historyManager: IHistoryProvider
): void {
    const queue = getQueue(sessionId);
    queue.pending = { answers, getToken: getTokenFn };
    startDrain(sessionId, historyManager);
}

/** Enqueue a final snapshot and wait until all pending saves for this session complete. */
export async function flushHistorySave(
    sessionId: string,
    answers: Answers,
    getTokenFn: () => Promise<string | undefined>,
    historyManager: IHistoryProvider
): Promise<void> {
    enqueueHistorySave(sessionId, answers, getTokenFn, historyManager);
    const queue = getQueue(sessionId);
    if (queue.processPromise) {
        await queue.processPromise;
    }
}
