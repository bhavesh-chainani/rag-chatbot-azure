const BACKEND_URI = "";

import {
    ChatAppResponse,
    ChatAppResponseOrError,
    ChatAppRequest,
    Config,
    SimpleAPIResponse,
    HistoryListApiResponse,
    HistoryApiResponse,
    PBSGCaseSummaryResponse
} from "./models";
import { useLogin, getToken, isUsingAppServicesLogin } from "../authConfig";

export async function getHeaders(idToken: string | undefined): Promise<Record<string, string>> {
    // If using login and not using app services, add the id token of the logged in account as the authorization
    if (useLogin && !isUsingAppServicesLogin) {
        if (idToken) {
            return { Authorization: `Bearer ${idToken}` };
        }
    }

    return {};
}

export async function configApi(): Promise<Config> {
    const response = await fetch(`${BACKEND_URI}/config`, {
        method: "GET"
    });

    return (await response.json()) as Config;
}

export async function chatApi(request: ChatAppRequest, shouldStream: boolean, idToken: string | undefined, signal: AbortSignal): Promise<Response> {
    let url = `${BACKEND_URI}/chat`;
    if (shouldStream) {
        url += "/stream";
    }
    const headers = await getHeaders(idToken);
    return await fetch(url, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: signal
    });
}

export async function pbsgCaseSummaryApi(request: ChatAppRequest, idToken: string | undefined, signal?: AbortSignal): Promise<PBSGCaseSummaryResponse> {
    const headers = await getHeaders(idToken);
    const response = await fetch(`${BACKEND_URI}/chat/summary`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal
    });
    if (!response.ok) {
        throw new Error(`Case summary request failed with status ${response.status}`);
    }
    return (await response.json()) as PBSGCaseSummaryResponse;
}

export async function getSpeechApi(text: string): Promise<string | null> {
    return await fetch("/speech", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            text: text
        })
    })
        .then(response => {
            if (response.status == 200) {
                return response.blob();
            } else if (response.status == 400) {
                console.log("Speech synthesis is not enabled.");
                return null;
            } else {
                console.error("Unable to get speech synthesis.");
                return null;
            }
        })
        .then(blob => (blob ? URL.createObjectURL(blob) : null));
}

export function getCitationFilePath(citation: string): string {
    // If there are parentheses at end of citation, remove part in parentheses
    const cleanedCitation = citation.replace(/\s*\(.*?\)\s*$/, "").trim();
    return `${BACKEND_URI}/content/${cleanedCitation}`;
}

export async function uploadFileApi(request: FormData, idToken: string): Promise<SimpleAPIResponse> {
    const response = await fetch("/upload", {
        method: "POST",
        headers: await getHeaders(idToken),
        body: request
    });

    if (!response.ok) {
        throw new Error(`Uploading files failed: ${response.statusText}`);
    }

    const dataResponse: SimpleAPIResponse = await response.json();
    return dataResponse;
}

export async function deleteUploadedFileApi(filename: string, idToken: string): Promise<SimpleAPIResponse> {
    const headers = await getHeaders(idToken);
    const response = await fetch("/delete_uploaded", {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ filename })
    });

    if (!response.ok) {
        throw new Error(`Deleting file failed: ${response.statusText}`);
    }

    const dataResponse: SimpleAPIResponse = await response.json();
    return dataResponse;
}

export async function listUploadedFilesApi(idToken: string): Promise<string[]> {
    const response = await fetch(`/list_uploaded`, {
        method: "GET",
        headers: await getHeaders(idToken)
    });

    if (!response.ok) {
        throw new Error(`Listing files failed: ${response.statusText}`);
    }

    const dataResponse: string[] = await response.json();
    return dataResponse;
}

export async function generateChatTitleApi(message: string, idToken: string): Promise<string> {
    const headers = await getHeaders(idToken);
    const response = await fetch(`${BACKEND_URI}/chat_history/title`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ message })
    });

    if (!response.ok) {
        return message.length > 50 ? message.substring(0, 50) + "..." : message;
    }

    const dataResponse = await response.json();
    return dataResponse.title || message.substring(0, 50);
}

export async function postChatHistoryApi(item: any, idToken: string): Promise<any> {
    const headers = await getHeaders(idToken);
    const response = await fetch("/chat_history", {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(item)
    });

    if (!response.ok) {
        throw new Error(`Posting chat history failed: ${response.statusText}`);
    }

    const dataResponse: any = await response.json();
    return dataResponse;
}

export async function getChatHistoryListApi(count: number, continuationToken: string | undefined, idToken: string): Promise<HistoryListApiResponse> {
    const headers = await getHeaders(idToken);
    let url = `${BACKEND_URI}/chat_history/sessions?count=${count}`;
    if (continuationToken) {
        url += `&continuation_token=${continuationToken}`;
    }

    const response = await fetch(url.toString(), {
        method: "GET",
        headers: { ...headers, "Content-Type": "application/json" }
    });

    if (!response.ok) {
        throw new Error(`Getting chat histories failed: ${response.statusText}`);
    }

    const dataResponse: HistoryListApiResponse = await response.json();
    return dataResponse;
}

export async function searchChatHistoryListApi(query: string, count: number, idToken: string): Promise<HistoryListApiResponse> {
    const headers = await getHeaders(idToken);
    const encodedQuery = encodeURIComponent(query);
    const url = `${BACKEND_URI}/chat_history/sessions/search?q=${encodedQuery}&count=${count}`;

    const response = await fetch(url.toString(), {
        method: "GET",
        headers: { ...headers, "Content-Type": "application/json" }
    });

    if (!response.ok) {
        throw new Error(`Searching chat histories failed: ${response.statusText}`);
    }

    const dataResponse: HistoryListApiResponse = await response.json();
    return dataResponse;
}

export async function getChatHistoryApi(id: string, idToken: string): Promise<HistoryApiResponse> {
    const headers = await getHeaders(idToken);
    const response = await fetch(`/chat_history/sessions/${id}`, {
        method: "GET",
        headers: { ...headers, "Content-Type": "application/json" }
    });

    if (!response.ok) {
        throw new Error(`Getting chat history failed: ${response.statusText}`);
    }

    const dataResponse: HistoryApiResponse = await response.json();
    return dataResponse;
}

export async function renameChatHistoryApi(id: string, title: string, idToken: string): Promise<SimpleAPIResponse> {
    const headers = await getHeaders(idToken);
    const response = await fetch(`/chat_history/sessions/${id}`, {
        method: "PATCH",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ title })
    });

    if (!response.ok) {
        throw new Error(`Renaming chat history failed: ${response.statusText}`);
    }

    return (await response.json()) as SimpleAPIResponse;
}

export async function deleteChatHistoryApi(id: string, idToken: string): Promise<any> {
    const headers = await getHeaders(idToken);
    const response = await fetch(`/chat_history/sessions/${id}`, {
        method: "DELETE",
        headers: { ...headers, "Content-Type": "application/json" }
    });

    if (!response.ok) {
        throw new Error(`Deleting chat history failed: ${response.statusText}`);
    }
}
