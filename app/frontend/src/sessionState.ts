const HISTORY_SESSION_ID_KEY = "history_session_id";

export function getHistorySessionId(sessionState: unknown): string | null {
    if (typeof sessionState === "string") {
        const trimmed = sessionState.trim();
        return trimmed ? trimmed : null;
    }

    if (sessionState && typeof sessionState === "object") {
        const historySessionId = (sessionState as Record<string, unknown>)[HISTORY_SESSION_ID_KEY];
        if (typeof historySessionId === "string") {
            const trimmed = historySessionId.trim();
            return trimmed ? trimmed : null;
        }
    }

    return null;
}

export function withHistorySessionId(sessionState: unknown, historySessionId: string): unknown {
    if (!historySessionId) {
        return sessionState;
    }

    if (sessionState && typeof sessionState === "object") {
        return {
            ...(sessionState as Record<string, unknown>),
            [HISTORY_SESSION_ID_KEY]: historySessionId
        };
    }

    return {
        [HISTORY_SESSION_ID_KEY]: historySessionId
    };
}
