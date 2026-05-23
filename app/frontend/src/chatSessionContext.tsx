import { createContext, useCallback, useContext, useRef, type ReactNode } from "react";

type ClearChatFn = () => void | Promise<void>;

type ChatSessionContextValue = {
    registerClearChat: (fn: ClearChatFn | null) => void;
    clearChatFromHeader: () => void;
};

const ChatSessionContext = createContext<ChatSessionContextValue | null>(null);

export function ChatSessionProvider({ children }: { children: ReactNode }) {
    const clearChatRef = useRef<ClearChatFn | null>(null);

    const registerClearChat = useCallback((fn: ClearChatFn | null) => {
        clearChatRef.current = fn;
    }, []);

    const clearChatFromHeader = useCallback(() => {
        void clearChatRef.current?.();
    }, []);

    return (
        <ChatSessionContext.Provider value={{ registerClearChat, clearChatFromHeader }}>
            {children}
        </ChatSessionContext.Provider>
    );
}

export function useChatSession(): ChatSessionContextValue {
    const ctx = useContext(ChatSessionContext);
    if (!ctx) {
        throw new Error("useChatSession must be used within ChatSessionProvider");
    }
    return ctx;
}
