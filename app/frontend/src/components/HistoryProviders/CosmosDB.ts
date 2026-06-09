import { IHistoryProvider, Answers, HistoryProviderOptions, HistoryMetaData } from "./IProvider";
import {
    deleteChatHistoryApi,
    getChatHistoryApi,
    getChatHistoryListApi,
    postChatHistoryApi,
    renameChatHistoryApi,
    searchChatHistoryListApi
} from "../../api";

export class CosmosDBProvider implements IHistoryProvider {
    getProviderName = () => HistoryProviderOptions.CosmosDB;

    private continuationToken: string | undefined;
    private isItemEnd: boolean = false;

    resetContinuationToken() {
        this.continuationToken = undefined;
        this.isItemEnd = false;
    }

    async getNextItems(count: number, idToken?: string): Promise<HistoryMetaData[]> {
        if (this.isItemEnd) {
            return [];
        }

        try {
            const response = await getChatHistoryListApi(count, this.continuationToken, idToken || "");
            this.continuationToken = response.continuation_token;
            if (!this.continuationToken) {
                this.isItemEnd = true;
            }
            return response.sessions.map(session => ({
                id: session.id,
                title: session.title,
                timestamp: session.timestamp
            }));
        } catch (e) {
            console.error(e);
            return [];
        }
    }

    async searchItems(query: string, count: number, idToken?: string): Promise<HistoryMetaData[]> {
        try {
            const response = await searchChatHistoryListApi(query, count, idToken || "");
            return response.sessions.map(session => ({
                id: session.id,
                title: session.title,
                timestamp: session.timestamp
            }));
        } catch (e) {
            console.error(e);
            return [];
        }
    }

    async addItem(id: string, answers: Answers, idToken?: string): Promise<void> {
        await postChatHistoryApi({ id, answers }, idToken || "");
        return;
    }

    async getItem(id: string, idToken?: string): Promise<Answers | null> {
        const response = await getChatHistoryApi(id, idToken || "");
        return response.answers || null;
    }

    async renameItem(id: string, title: string, idToken?: string): Promise<void> {
        await renameChatHistoryApi(id, title, idToken || "");
        return;
    }

    async deleteItem(id: string, idToken?: string): Promise<void> {
        await deleteChatHistoryApi(id, idToken || "");
        return;
    }
}
