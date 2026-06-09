import { IHistoryProvider, Answers, HistoryProviderOptions, HistoryMetaData } from "./IProvider";

export class NoneProvider implements IHistoryProvider {
    getProviderName = () => HistoryProviderOptions.None;
    resetContinuationToken(): void {
        return;
    }
    async getNextItems(count: number): Promise<HistoryMetaData[]> {
        return [];
    }
    async searchItems(query: string, count: number): Promise<HistoryMetaData[]> {
        return [];
    }
    async addItem(id: string, answers: Answers): Promise<void> {
        return;
    }
    async getItem(id: string): Promise<null> {
        return null;
    }
    async renameItem(id: string, title: string): Promise<void> {
        return;
    }
    async deleteItem(id: string): Promise<void> {
        return;
    }
}
