import { useRef, useState, useEffect, useContext, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Helmet } from "react-helmet-async";
import {
    OverlayDrawer,
    DrawerHeader,
    DrawerHeaderTitle,
    DrawerBody,
    Button,
    type DialogOpenChangeEvent,
    type DialogOpenChangeData
} from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import readNDJSONStream from "ndjson-readablestream";

import appLogo from "../../assets/pbsg_logo.png";
import styles from "./Chat.module.css";

import {
    chatApi,
    configApi,
    pbsgCaseSummaryApi,
    RetrievalMode,
    ChatAppResponse,
    ChatAppResponseOrError,
    ChatAppRequest,
    ResponseMessage,
    SpeechConfig
} from "../../api";
import { Answer, AnswerError, AnswerLoading } from "../../components/Answer";
import { QuestionInput } from "../../components/QuestionInput";
import { ExampleList } from "../../components/Example";
import { UserChatMessage } from "../../components/UserChatMessage";
import { AnalysisPanel, AnalysisPanelTabs } from "../../components/AnalysisPanel";
import { HistoryPanel } from "../../components/HistoryPanel";
import { Answers, HistoryProviderOptions, useHistoryManager } from "../../components/HistoryProviders";
import { HistoryButton } from "../../components/HistoryButton";
import { SettingsButton } from "../../components/SettingsButton";
import { ClearChatButton } from "../../components/ClearChatButton";
import { NewChatButton } from "../../components/NewChatButton";
import { UploadFile } from "../../components/UploadFile";
import { useLogin, getToken, requireAccessControl } from "../../authConfig";
import { useMsal } from "@azure/msal-react";
import { TokenClaimsDisplay } from "../../components/TokenClaimsDisplay";
import { LoginContext } from "../../loginContext";
import { LanguagePicker } from "../../i18n/LanguagePicker";
import { Settings } from "../../components/Settings/Settings";
import { enqueueHistorySave, flushHistorySave } from "../../chatHistorySaveQueue";
import { useChatSession } from "../../chatSessionContext";

function normalizeHistoryAnswers(sessionId: string, answers: Answers): Answers {
    return answers.map(([question, response, sentAt]) => {
        const normalized: [string, ChatAppResponse, number?] = [
            question,
            { ...response, session_state: sessionId }
        ];
        if (sentAt !== undefined) {
            normalized[2] = sentAt;
        }
        return normalized;
    });
}

function useCitationPanelIframeHeight(): string {
    const [height, setHeight] = useState("810px");
    useEffect(() => {
        const update = () => {
            if (window.matchMedia("(max-width: 991px)").matches) {
                setHeight(`${Math.max(240, Math.round(window.innerHeight * 0.48))}px`);
            } else {
                setHeight("810px");
            }
        };
        update();
        const mq = window.matchMedia("(max-width: 991px)");
        mq.addEventListener("change", update);
        window.addEventListener("resize", update);
        return () => {
            mq.removeEventListener("change", update);
            window.removeEventListener("resize", update);
        };
    }, []);
    return height;
}

const Chat = () => {
    const { t, i18n } = useTranslation();
    const [isConfigPanelOpen, setIsConfigPanelOpen] = useState(false);
    const [isHistoryPanelOpen, setIsHistoryPanelOpen] = useState(false);
    const [promptTemplate, setPromptTemplate] = useState<string>("");
    const [temperature, setTemperature] = useState<number>(0.3);
    const [seed, setSeed] = useState<number | null>(null);
    const [minimumRerankerScore, setMinimumRerankerScore] = useState<number>(1.9);
    const [minimumSearchScore, setMinimumSearchScore] = useState<number>(0);
    const [retrieveCount, setRetrieveCount] = useState<number>(3);
    const [agenticReasoningEffort, setRetrievalReasoningEffort] = useState<string>("minimal");
    const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>(RetrievalMode.Hybrid);
    const [useSemanticRanker, setUseSemanticRanker] = useState<boolean>(true);
    const [useQueryRewriting, setUseQueryRewriting] = useState<boolean>(false);
    const [reasoningEffort, setReasoningEffort] = useState<string>("");
    const [streamingEnabled, setStreamingEnabled] = useState<boolean>(true);
    const [shouldStream, setShouldStream] = useState<boolean>(true);
    const previousShouldStreamRef = useRef<boolean>(true);
    const forcedStreamingRef = useRef<boolean>(false);
    const [useSemanticCaptions, setUseSemanticCaptions] = useState<boolean>(false);
    const [includeCategory, setIncludeCategory] = useState<string>("");
    const [excludeCategory, setExcludeCategory] = useState<string>("");
    const [useSuggestFollowupQuestions, setUseSuggestFollowupQuestions] = useState<boolean>(false);
    const [searchTextEmbeddings, setSearchTextEmbeddings] = useState<boolean>(true);
    const [searchImageEmbeddings, setSearchImageEmbeddings] = useState<boolean>(false);
    const [sendTextSources, setSendTextSources] = useState<boolean>(true);
    const [sendImageSources, setSendImageSources] = useState<boolean>(false);

    const lastQuestionRef = useRef<string>("");
    const chatMessageStreamEnd = useRef<HTMLDivElement | null>(null);
    const clearingForNewChatRef = useRef<boolean>(false);
    const chatEpochRef = useRef<number>(0);

    const [isLoading, setIsLoading] = useState<boolean>(false);
    const [isStreaming, setIsStreaming] = useState<boolean>(false);
    const [abortController, setAbortController] = useState<AbortController | null>(null);
    const [restoredQuestion, setRestoredQuestion] = useState<string>("");
    const [error, setError] = useState<unknown>();

    const [activeCitation, setActiveCitation] = useState<string>();
    const [activeAnalysisPanelTab, setActiveAnalysisPanelTab] = useState<AnalysisPanelTabs | undefined>(undefined);

    const [selectedAnswer, setSelectedAnswer] = useState<number>(0);
    const [answers, setAnswers] = useState<Answers>([]);
    const answersRef = useRef<Answers>([]);
    const [streamedAnswers, setStreamedAnswers] = useState<Answers>([]);
    const [pendingQuestionSentAt, setPendingQuestionSentAt] = useState<number | null>(null);
    const [speechUrls, setSpeechUrls] = useState<(string | null)[]>([]);

    const [showMultimodalOptions, setShowMultimodalOptions] = useState<boolean>(false);
    const [showSemanticRankerOption, setShowSemanticRankerOption] = useState<boolean>(false);
    const [showQueryRewritingOption, setShowQueryRewritingOption] = useState<boolean>(false);
    const [showReasoningEffortOption, setShowReasoningEffortOption] = useState<boolean>(false);
    const [showVectorOption, setShowVectorOption] = useState<boolean>(false);
    const [showUserUpload, setShowUserUpload] = useState<boolean>(false);
    const [showLanguagePicker, setshowLanguagePicker] = useState<boolean>(false);
    const [showSpeechInput, setShowSpeechInput] = useState<boolean>(false);
    const [showSpeechOutputBrowser, setShowSpeechOutputBrowser] = useState<boolean>(false);
    const [showSpeechOutputAzure, setShowSpeechOutputAzure] = useState<boolean>(false);
    const [showChatHistoryBrowser, setShowChatHistoryBrowser] = useState<boolean>(false);
    const [showChatHistoryCosmos, setShowChatHistoryCosmos] = useState<boolean>(false);
    const [showAgenticRetrievalOption, setShowAgenticRetrievalOption] = useState<boolean>(false);
    const [webSourceSupported, setWebSourceSupported] = useState<boolean>(false);
    const [webSourceEnabled, setWebSourceEnabled] = useState<boolean>(false);
    const [sharePointSourceSupported, setSharePointSourceSupported] = useState<boolean>(false);
    const [sharePointSourceEnabled, setSharePointSourceEnabled] = useState<boolean>(false);
    const [useAgenticKnowledgeBase, setUseAgenticRetrieval] = useState<boolean>(false);
    const [hideMinimalRetrievalReasoningOption, setHideMinimalRetrievalReasoningOption] = useState<boolean>(false);
    const streamingDisabledByOverrides = useAgenticKnowledgeBase && webSourceEnabled;

    const audio = useRef(new Audio()).current;
    const [isPlaying, setIsPlaying] = useState(false);

    const speechConfig: SpeechConfig = {
        speechUrls,
        setSpeechUrls,
        audio,
        isPlaying,
        setIsPlaying
    };

    const getConfig = async () => {
        configApi().then(config => {
            setShowMultimodalOptions(config.showMultimodalOptions);
            if (config.showMultimodalOptions) {
                // Initialize from server config so defaults match deployment settings
                setSendTextSources(config.ragSendTextSources !== undefined ? config.ragSendTextSources : true);
                setSendImageSources(config.ragSendImageSources);
                setSearchTextEmbeddings(config.ragSearchTextEmbeddings);
                setSearchImageEmbeddings(config.ragSearchImageEmbeddings);
            }
            setUseSemanticRanker(config.showSemanticRankerOption);
            setShowSemanticRankerOption(config.showSemanticRankerOption);
            setUseQueryRewriting(config.showQueryRewritingOption);
            setShowQueryRewritingOption(config.showQueryRewritingOption);
            setShowReasoningEffortOption(config.showReasoningEffortOption);
            setStreamingEnabled(config.streamingEnabled);
            if (config.showReasoningEffortOption) {
                setReasoningEffort(config.defaultReasoningEffort);
            }
            setShowVectorOption(config.showVectorOption);
            if (!config.showVectorOption) {
                setRetrievalMode(RetrievalMode.Text);
            }
            setShowUserUpload(config.showUserUpload);
            setshowLanguagePicker(config.showLanguagePicker);
            setShowSpeechInput(config.showSpeechInput);
            setShowSpeechOutputBrowser(config.showSpeechOutputBrowser);
            setShowSpeechOutputAzure(config.showSpeechOutputAzure);
            setShowChatHistoryBrowser(config.showChatHistoryBrowser);
            setShowChatHistoryCosmos(config.showChatHistoryCosmos);
            setShowAgenticRetrievalOption(config.showAgenticRetrievalOption);
            setUseAgenticRetrieval(config.showAgenticRetrievalOption);
            setWebSourceSupported(config.webSourceEnabled);
            setWebSourceEnabled(config.webSourceEnabled);
            setSharePointSourceSupported(config.sharepointSourceEnabled);
            setSharePointSourceEnabled(config.sharepointSourceEnabled);
            if (config.showAgenticRetrievalOption) {
                setRetrieveCount(10);
            }
            const defaultRetrievalEffort = config.defaultRetrievalReasoningEffort ?? "minimal";
            setHideMinimalRetrievalReasoningOption(config.webSourceEnabled);
            setRetrievalReasoningEffort(defaultRetrievalEffort);
        });
    };

    const handleAsyncRequest = async (
        question: string,
        answers: Answers,
        responseBody: ReadableStream<any>,
        signal: AbortSignal,
        questionSentAt: number,
        requestEpoch: number
    ) => {
        let answer: string = "";
        let askResponse: ChatAppResponse = {
            message: { content: "", role: "assistant" },
            delta: { content: "", role: "assistant" },
            context: { data_points: { text: [], images: [], citations: [] }, thoughts: [], followup_questions: null },
            session_state: null
        };

        const updateState = (newContent: string) => {
            return new Promise(resolve => {
                setTimeout(() => {
                    if (signal.aborted || chatEpochRef.current !== requestEpoch) {
                        resolve(null);
                        return;
                    }
                    answer += newContent;
                    const latestResponse: ChatAppResponse = {
                        ...askResponse,
                        message: { content: answer, role: askResponse.message.role }
                    };
                    setStreamedAnswers([...answers, [question, latestResponse, questionSentAt]]);
                    resolve(null);
                }, 33);
            });
        };
        try {
            setIsStreaming(true);
            for await (const event of readNDJSONStream(responseBody)) {
                if (signal.aborted) {
                    break;
                }
                if (event["context"] && event["context"]["data_points"]) {
                    event["message"] = event["delta"];
                    askResponse = event as ChatAppResponse;
                } else if (event["delta"] && event["delta"]["content"]) {
                    if (chatEpochRef.current === requestEpoch) {
                        setIsLoading(false);
                    }
                    await updateState(event["delta"]["content"]);
                } else if (event["context"]) {
                    // Update context with new keys from latest event
                    askResponse.context = { ...askResponse.context, ...event["context"] };
                } else if (event["error"]) {
                    throw Error(event["error"]);
                }
            }
        } catch (e) {
            if (e instanceof DOMException && e.name === "AbortError") {
                // User clicked stop - don't treat as error
                console.log("Stream aborted by user");
            } else {
                throw e; // Re-throw other errors to be caught by makeApiRequest
            }
        } finally {
            if (chatEpochRef.current === requestEpoch) {
                setIsStreaming(false);
            }
        }
        const fullResponse: ChatAppResponse = {
            ...askResponse,
            message: { content: answer, role: askResponse.message.role }
        };
        return fullResponse;
    };

    const client = useLogin ? useMsal().instance : undefined;
    const { loggedIn } = useContext(LoginContext);

    const historyProvider: HistoryProviderOptions = (() => {
        if (useLogin && showChatHistoryCosmos) return HistoryProviderOptions.CosmosDB;
        if (showChatHistoryBrowser) return HistoryProviderOptions.IndexedDB;
        return HistoryProviderOptions.None;
    })();
    const historyManager = useHistoryManager(historyProvider);
    const { registerClearChat } = useChatSession();

    useEffect(() => {
        answersRef.current = answers;
    }, [answers]);

    const getHistoryToken = useCallback(async (): Promise<string | undefined> => {
        return client ? await getToken(client) : undefined;
    }, [client]);

    const refreshCaseSummary = useCallback(
        async (currentAnswers: Answers, answerSentAt: number | undefined, requestEpoch: number, idToken: string | undefined) => {
            const latestAnswer = currentAnswers[currentAnswers.length - 1]?.[1];
            const summary = latestAnswer?.context?.pbsg_case_summary;
            if (!summary || summary.status !== "pending") {
                return;
            }
            const messages: ResponseMessage[] = currentAnswers.flatMap(a => [
                { content: a[0], role: "user" },
                { content: a[1].message.content, role: "assistant" }
            ]);
            try {
                const response = await pbsgCaseSummaryApi(
                    {
                        messages,
                        context: {
                            overrides: {
                                send_text_sources: sendTextSources,
                                send_image_sources: sendImageSources,
                                search_text_embeddings: searchTextEmbeddings,
                                search_image_embeddings: searchImageEmbeddings,
                                language: i18n.language,
                                use_agentic_knowledgebase: useAgenticKnowledgeBase
                            }
                        },
                        session_state: latestAnswer.session_state
                    },
                    idToken
                );
                if (chatEpochRef.current !== requestEpoch) {
                    return;
                }
                const updatedAnswers = answersRef.current.map(answerTuple => {
                    const [userQuestion, answerResponse, sentAt] = answerTuple;
                    if (sentAt !== answerSentAt) {
                        return answerTuple;
                    }
                    const updatedResponse: ChatAppResponse = {
                        ...answerResponse,
                        context: {
                            ...answerResponse.context,
                            pbsg_case_summary: response.pbsg_case_summary
                        }
                    };
                    return [userQuestion, updatedResponse, sentAt] as [string, ChatAppResponse, number?];
                });
                setAnswers(updatedAnswers);
                answersRef.current = updatedAnswers;
            } catch (e) {
                console.warn("Unable to refresh PBSG case summary", e);
            }
        },
        [
            i18n.language,
            searchImageEmbeddings,
            searchTextEmbeddings,
            sendImageSources,
            sendTextSources,
            useAgenticKnowledgeBase
        ]
    );

    const updateStreamingPreference = (isStreamingEnabledOverride: boolean, disablesStreamingOverride: boolean) => {
        if (!isStreamingEnabledOverride) {
            setShouldStream(current => {
                if (!forcedStreamingRef.current) {
                    previousShouldStreamRef.current = current;
                }
                forcedStreamingRef.current = true;
                return current ? false : current;
            });
            return;
        }

        if (disablesStreamingOverride) {
            setShouldStream(current => {
                if (!forcedStreamingRef.current) {
                    previousShouldStreamRef.current = current;
                }
                forcedStreamingRef.current = true;
                return current ? false : current;
            });
            return;
        }

        forcedStreamingRef.current = false;
        setShouldStream(current => {
            const desiredShouldStream = previousShouldStreamRef.current;
            return current === desiredShouldStream ? current : desiredShouldStream;
        });
    };

    const makeApiRequest = async (question: string) => {
        const controller = new AbortController();
        const requestEpoch = chatEpochRef.current;
        setAbortController(controller);
        clearingForNewChatRef.current = false;
        lastQuestionRef.current = question;
        const questionSentAt = Date.now();
        setPendingQuestionSentAt(questionSentAt);

        error && setError(undefined);
        setRestoredQuestion("");
        setIsLoading(true);
        setActiveCitation(undefined);
        setActiveAnalysisPanelTab(undefined);

        const token = client ? await getToken(client) : undefined;
        if (chatEpochRef.current !== requestEpoch) {
            return;
        }

        try {
            const requestAnswers = [...answersRef.current];
            const messages: ResponseMessage[] = requestAnswers.flatMap(a => [
                { content: a[0], role: "user" },
                { content: a[1].message.content, role: "assistant" }
            ]);

            const request: ChatAppRequest = {
                messages: [...messages, { content: question, role: "user" }],
                context: {
                    overrides: {
                        prompt_template: promptTemplate.length === 0 ? undefined : promptTemplate,
                        include_category: includeCategory.length === 0 ? undefined : includeCategory,
                        exclude_category: excludeCategory.length === 0 ? undefined : excludeCategory,
                        top: retrieveCount,
                        ...(useAgenticKnowledgeBase ? { retrieval_reasoning_effort: agenticReasoningEffort } : {}),
                        temperature: temperature,
                        minimum_reranker_score: minimumRerankerScore,
                        minimum_search_score: minimumSearchScore,
                        retrieval_mode: retrievalMode,
                        semantic_ranker: useSemanticRanker,
                        semantic_captions: useSemanticCaptions,
                        query_rewriting: useQueryRewriting,
                        reasoning_effort: reasoningEffort,
                        suggest_followup_questions: useSuggestFollowupQuestions,
                        search_text_embeddings: searchTextEmbeddings,
                        search_image_embeddings: searchImageEmbeddings,
                        send_text_sources: sendTextSources,
                        send_image_sources: sendImageSources,
                        language: i18n.language,
                        use_agentic_knowledgebase: useAgenticKnowledgeBase,
                        use_web_source: webSourceSupported ? webSourceEnabled : false,
                        use_sharepoint_source: sharePointSourceSupported ? sharePointSourceEnabled : false,
                        ...(seed !== null ? { seed: seed } : {})
                    }
                },
                // AI Chat Protocol: Client must pass on any session state received from the server
                session_state: requestAnswers.length ? requestAnswers[requestAnswers.length - 1][1].session_state : null
            };

            const response = await chatApi(request, shouldStream, token, controller.signal);
            if (!response.body) {
                throw Error("No response body");
            }
            if (response.status > 299 || !response.ok) {
                throw Error(`Request failed with status ${response.status}`);
            }
            if (shouldStream) {
                const parsedResponse: ChatAppResponse = await handleAsyncRequest(
                    question,
                    requestAnswers,
                    response.body,
                    controller.signal,
                    questionSentAt,
                    requestEpoch
                );
                if (chatEpochRef.current !== requestEpoch) {
                    return;
                }
                // Only add to answers if we got content, otherwise restore question to input
                if (parsedResponse.message.content) {
                    const newAnswers: Answers = [...requestAnswers, [question, parsedResponse, questionSentAt]];
                    setAnswers(newAnswers);
                    answersRef.current = newAnswers;
                    setPendingQuestionSentAt(null);
                    void refreshCaseSummary(newAnswers, questionSentAt, requestEpoch, token);
                    if (typeof parsedResponse.session_state === "string" && parsedResponse.session_state !== "") {
                        enqueueHistorySave(
                            parsedResponse.session_state,
                            newAnswers,
                            getHistoryToken,
                            historyManager
                        );
                    }
                } else {
                    // Stopped before any content arrived - restore question to input
                    lastQuestionRef.current = requestAnswers.length > 0 ? requestAnswers[requestAnswers.length - 1][0] : "";
                    setRestoredQuestion(question);
                    setPendingQuestionSentAt(null);
                }
            } else {
                const parsedResponse: ChatAppResponseOrError = await response.json();
                if (parsedResponse.error) {
                    throw Error(parsedResponse.error);
                }
                if (chatEpochRef.current !== requestEpoch) {
                    return;
                }
                const newAnswers: Answers = [...requestAnswers, [question, parsedResponse as ChatAppResponse, questionSentAt]];
                setAnswers(newAnswers);
                answersRef.current = newAnswers;
                setPendingQuestionSentAt(null);
                void refreshCaseSummary(newAnswers, questionSentAt, requestEpoch, token);
                if (typeof parsedResponse.session_state === "string" && parsedResponse.session_state !== "") {
                    enqueueHistorySave(parsedResponse.session_state, newAnswers, getHistoryToken, historyManager);
                }
            }
            setSpeechUrls(currentSpeechUrls => [...currentSpeechUrls, null]);
        } catch (e) {
            if (e instanceof DOMException && e.name === "AbortError") {
                // Restore question only if user clicked Stop; not when they started a new chat
                if (!clearingForNewChatRef.current && chatEpochRef.current === requestEpoch) {
                    const currentAnswers = answersRef.current;
                    lastQuestionRef.current = currentAnswers.length > 0 ? currentAnswers[currentAnswers.length - 1][0] : "";
                    setRestoredQuestion(question);
                    setPendingQuestionSentAt(null);
                }
            } else {
                if (chatEpochRef.current === requestEpoch) {
                    setError(e);
                }
            }
        } finally {
            if (chatEpochRef.current === requestEpoch) {
                setIsLoading(false);
                setAbortController(null);
            }
        }
    };

    const clearChat = useCallback(async () => {
        clearingForNewChatRef.current = true;
        chatEpochRef.current += 1;
        const hasStreaming = streamedAnswers.length > 0;
        const toSave: Answers = hasStreaming ? [...streamedAnswers] : [...answersRef.current];
        const lastResponse = toSave.length > 0 ? toSave[toSave.length - 1][1] : null;
        let sessionId: string | null =
            lastResponse && typeof lastResponse.session_state === "string" && lastResponse.session_state !== ""
                ? lastResponse.session_state
                : null;
        if (toSave.length > 0 && historyProvider !== HistoryProviderOptions.None && !sessionId && historyProvider === HistoryProviderOptions.IndexedDB) {
            sessionId = crypto.randomUUID();
        }

        if (abortController) {
            abortController.abort();
        }

        lastQuestionRef.current = "";
        error && setError(undefined);
        setActiveCitation(undefined);
        setActiveAnalysisPanelTab(undefined);
        setSelectedAnswer(0);
        setAnswers([]);
        answersRef.current = [];
        setSpeechUrls([]);
        setStreamedAnswers([]);
        setPendingQuestionSentAt(null);
        setIsLoading(false);
        setIsStreaming(false);
        setRestoredQuestion("");

        if (toSave.length > 0 && historyProvider !== HistoryProviderOptions.None && sessionId) {
            void flushHistorySave(sessionId, toSave, getHistoryToken, historyManager).catch(e => {
                console.error("Failed to save chat to history:", e);
            });
        }
    }, [
        abortController,
        error,
        getHistoryToken,
        historyManager,
        historyProvider,
        streamedAnswers
    ]);

    useEffect(() => {
        registerClearChat(clearChat);
        return () => registerClearChat(null);
    }, [clearChat, registerClearChat]);

    useEffect(() => chatMessageStreamEnd.current?.scrollIntoView({ behavior: "smooth" }), [isLoading]);
    useEffect(() => chatMessageStreamEnd.current?.scrollIntoView({ behavior: "auto" }), [streamedAnswers]);
    useEffect(() => {
        getConfig();
    }, []);

    // Preserve streaming preference when agentic retrieval forces streaming off.
    useEffect(() => {
        updateStreamingPreference(streamingEnabled, streamingDisabledByOverrides);
    }, [streamingDisabledByOverrides, streamingEnabled]);

    const handleSettingsChange = (field: string, value: any) => {
        switch (field) {
            case "promptTemplate":
                setPromptTemplate(value);
                break;
            case "temperature":
                setTemperature(value);
                break;
            case "seed":
                setSeed(value);
                break;
            case "minimumRerankerScore":
                setMinimumRerankerScore(value);
                break;
            case "minimumSearchScore":
                setMinimumSearchScore(value);
                break;
            case "retrieveCount":
                setRetrieveCount(value);
                break;
            case "agenticReasoningEffort": {
                setRetrievalReasoningEffort(value);
                // If selecting minimal while web source is enabled, disable web source
                if (value === "minimal" && webSourceEnabled) {
                    setWebSourceEnabled(false);
                    setHideMinimalRetrievalReasoningOption(false);
                    // Web source was disabled, so restore streaming
                    updateStreamingPreference(streamingEnabled, false);
                }
                break;
            }
            case "useSemanticRanker":
                setUseSemanticRanker(value);
                break;
            case "useQueryRewriting":
                setUseQueryRewriting(value);
                break;
            case "reasoningEffort":
                setReasoningEffort(value);
                break;
            case "useSemanticCaptions":
                setUseSemanticCaptions(value);
                break;
            case "excludeCategory":
                setExcludeCategory(value);
                break;
            case "includeCategory":
                setIncludeCategory(value);
                break;
            case "shouldStream":
                {
                    const normalizedShouldStream = !!value;
                    forcedStreamingRef.current = false;
                    previousShouldStreamRef.current = normalizedShouldStream;
                    setShouldStream(normalizedShouldStream);
                }
                break;
            case "useSuggestFollowupQuestions":
                setUseSuggestFollowupQuestions(value);
                break;
            case "llmInputs":
                break;
            case "sendTextSources":
                setSendTextSources(value);
                break;
            case "sendImageSources":
                setSendImageSources(value);
                break;
            case "searchTextEmbeddings":
                setSearchTextEmbeddings(value);
                break;
            case "searchImageEmbeddings":
                setSearchImageEmbeddings(value);
                break;
            case "retrievalMode":
                setRetrievalMode(value);
                break;
            case "useAgenticKnowledgeBase": {
                setUseAgenticRetrieval(value);
                let effectiveWebSource = webSourceEnabled;
                if (!value && webSourceEnabled) {
                    effectiveWebSource = false;
                    setWebSourceEnabled(false);
                    setHideMinimalRetrievalReasoningOption(false);
                }
                // Only web source disables streaming
                const shouldDisableStreaming = !!value && effectiveWebSource;
                updateStreamingPreference(streamingEnabled, shouldDisableStreaming);
                break;
            }
            case "useWebSource":
                if (!webSourceSupported) {
                    setWebSourceEnabled(false);
                    return;
                }
                const normalizedWebSource = !!value;
                setWebSourceEnabled(normalizedWebSource);
                setHideMinimalRetrievalReasoningOption(normalizedWebSource);
                // When enabling web source, disable follow-up questions and streaming
                if (normalizedWebSource) {
                    setUseSuggestFollowupQuestions(false);
                }
                const shouldDisableStreaming = useAgenticKnowledgeBase && normalizedWebSource;
                updateStreamingPreference(streamingEnabled, shouldDisableStreaming);
                break;
            case "useSharePointSource":
                if (!sharePointSourceSupported) {
                    setSharePointSourceEnabled(false);
                    return;
                }
                setSharePointSourceEnabled(!!value);
                break;
        }
    };

    const onExampleClicked = (example: string) => {
        makeApiRequest(example);
    };

    const onQuickReplyClicked = (value: string) => {
        makeApiRequest(value);
    };

    const onShowCitation = (citation: string, index: number) => {
        if (activeCitation === citation && activeAnalysisPanelTab === AnalysisPanelTabs.CitationTab && selectedAnswer === index) {
            setActiveAnalysisPanelTab(undefined);
        } else {
            setActiveCitation(citation);
            setActiveAnalysisPanelTab(AnalysisPanelTabs.CitationTab);
        }

        setSelectedAnswer(index);
    };

    const onToggleTab = (tab: AnalysisPanelTabs, index: number) => {
        if (activeAnalysisPanelTab === tab && selectedAnswer === index) {
            setActiveAnalysisPanelTab(undefined);
        } else {
            setActiveAnalysisPanelTab(tab);
        }

        setSelectedAnswer(index);
    };

    const onStopClick = async () => {
        try {
            if (abortController) {
                abortController.abort();
            }
        } catch (e) {
            console.log("An error occurred trying to stop the stream: ", e);
        }
    };

    const citationPanelIframeHeight = useCitationPanelIframeHeight();

    return (
        <div className={styles.container}>
            {/* Setting the page title using react-helmet-async */}
            <Helmet>
                <title>{t("pageTitle")}</title>
            </Helmet>
            <div className={styles.commandsSplitContainer}>
                <div className={styles.commandsContainer}>
                    {((useLogin && showChatHistoryCosmos) || showChatHistoryBrowser) && (
                        <HistoryButton className={styles.commandButton} onClick={() => setIsHistoryPanelOpen(!isHistoryPanelOpen)} />
                    )}
                </div>
                <div className={`${styles.commandsContainer} ${styles.commandsContainerActions}`}>
                    <NewChatButton className={styles.commandButton} onClick={clearChat} disabled={isLoading} />
                    <ClearChatButton className={styles.commandButton} onClick={clearChat} disabled={!lastQuestionRef.current || isLoading} />
                    {showUserUpload && <UploadFile className={styles.commandButton} disabled={!loggedIn} />}
                    {/* <SettingsButton className={styles.commandButton} onClick={() => setIsConfigPanelOpen(!isConfigPanelOpen)} /> */}
                </div>
            </div>
            <div
                className={`${styles.chatRoot}${isHistoryPanelOpen ? ` ${styles.chatRootHistoryOpen}` : ""}`}
            >
                <div className={styles.chatMain}>
                    <div className={styles.chatContainer}>
                    {!lastQuestionRef.current ? (
                        <div className={styles.chatEmptyState}>
                            <img src={appLogo} alt="Pro Bono SG" style={{ height: "100px", width: "auto" }} />

                            <h1 className={styles.chatEmptyStateTitle}>{t("chatEmptyStateTitle")}</h1>
                            <h2 className={styles.chatEmptyStateSubtitle}>{t("chatEmptyStateSubtitle")}</h2>
                            {showLanguagePicker && <LanguagePicker onLanguageChange={newLang => i18n.changeLanguage(newLang)} />}

                            <ExampleList onExampleClicked={onExampleClicked} useMultimodalAnswering={showMultimodalOptions} />
                        </div>
                    ) : (
                        <div className={styles.chatMessageStream}>
                            {isStreaming &&
                                streamedAnswers.map((streamedAnswer, index) => (
                                    <div key={index}>
                                        <UserChatMessage message={streamedAnswer[0]} sentAt={streamedAnswer[2]} />
                                        <div className={styles.chatMessageGpt}>
                                            <Answer
                                                isStreaming={true}
                                                key={index}
                                                answer={streamedAnswer[1]}
                                                index={index}
                                                speechConfig={speechConfig}
                                                isSelected={false}
                                                onCitationClicked={c => onShowCitation(c, index)}
                                                onThoughtProcessClicked={() => onToggleTab(AnalysisPanelTabs.ThoughtProcessTab, index)}
                                                onSupportingContentClicked={() => onToggleTab(AnalysisPanelTabs.SupportingContentTab, index)}
                                                onFollowupQuestionClicked={q => makeApiRequest(q)}
                                                onQuickReplyClicked={onQuickReplyClicked}
                                                showFollowupQuestions={useSuggestFollowupQuestions && answers.length - 1 === index}
                                                showQuickReplies={false}
                                                showSpeechOutputAzure={showSpeechOutputAzure}
                                                showSpeechOutputBrowser={showSpeechOutputBrowser}
                                            />
                                        </div>
                                    </div>
                                ))}
                            {!isStreaming &&
                                answers.map((answer, index) => (
                                    <div key={index}>
                                        <UserChatMessage message={answer[0]} sentAt={answer[2]} />
                                        <div className={styles.chatMessageGpt}>
                                            <Answer
                                                isStreaming={false}
                                                key={index}
                                                answer={answer[1]}
                                                index={index}
                                                speechConfig={speechConfig}
                                                isSelected={selectedAnswer === index && activeAnalysisPanelTab !== undefined}
                                                onCitationClicked={c => onShowCitation(c, index)}
                                                onThoughtProcessClicked={() => onToggleTab(AnalysisPanelTabs.ThoughtProcessTab, index)}
                                                onSupportingContentClicked={() => onToggleTab(AnalysisPanelTabs.SupportingContentTab, index)}
                                                onFollowupQuestionClicked={q => makeApiRequest(q)}
                                                onQuickReplyClicked={onQuickReplyClicked}
                                                showFollowupQuestions={useSuggestFollowupQuestions && answers.length - 1 === index}
                                                showQuickReplies={!isLoading && answers.length - 1 === index}
                                                showSpeechOutputAzure={showSpeechOutputAzure}
                                                showSpeechOutputBrowser={showSpeechOutputBrowser}
                                            />
                                        </div>
                                    </div>
                                ))}
                            {isLoading && (
                                <>
                                    <UserChatMessage message={lastQuestionRef.current} sentAt={pendingQuestionSentAt ?? undefined} />
                                    <div className={styles.chatMessageGptMinWidth}>
                                        <AnswerLoading />
                                    </div>
                                </>
                            )}
                            {error ? (
                                <>
                                    <UserChatMessage message={lastQuestionRef.current} sentAt={pendingQuestionSentAt ?? undefined} />
                                    <div className={styles.chatMessageGptMinWidth}>
                                        <AnswerError error={error.toString()} onRetry={() => makeApiRequest(lastQuestionRef.current)} />
                                    </div>
                                </>
                            ) : null}
                            <div ref={chatMessageStreamEnd} />
                        </div>
                    )}

                    <div className={styles.chatInput}>
                        <QuestionInput
                            clearOnSend
                            placeholder={t("defaultExamples.placeholder")}
                            disabled={isLoading}
                            onSend={question => makeApiRequest(question)}
                            showSpeechInput={showSpeechInput}
                            isStreaming={isStreaming}
                            isLoading={isLoading}
                            onStop={onStopClick}
                            initQuestion={restoredQuestion}
                        />
                    </div>
                    </div>

                    {answers.length > 0 && activeAnalysisPanelTab && (
                        <AnalysisPanel
                            className={styles.chatAnalysisPanel}
                            activeCitation={activeCitation}
                            onActiveTabChanged={x => onToggleTab(x, selectedAnswer)}
                            citationHeight={citationPanelIframeHeight}
                            answer={answers[selectedAnswer][1]}
                            activeTab={activeAnalysisPanelTab}
                            onCitationClicked={c => onShowCitation(c, selectedAnswer)}
                        />
                    )}
                </div>

                {((useLogin && showChatHistoryCosmos) || showChatHistoryBrowser) && (
                    <HistoryPanel
                        provider={historyProvider}
                        isOpen={isHistoryPanelOpen}
                        notify={!isStreaming && !isLoading}
                        onClose={() => setIsHistoryPanelOpen(false)}
                        onChatSelected={(sessionId, answers) => {
                            if (answers.length === 0) return;
                            chatEpochRef.current += 1;
                            clearingForNewChatRef.current = false;
                            const normalized = normalizeHistoryAnswers(sessionId, answers);
                            setAnswers(normalized);
                            answersRef.current = normalized;
                            setPendingQuestionSentAt(null);
                            setStreamedAnswers([]);
                            setSelectedAnswer(0);
                            setActiveCitation(undefined);
                            setActiveAnalysisPanelTab(undefined);
                            lastQuestionRef.current = normalized[normalized.length - 1][0];
                        }}
                    />
                )}

                <OverlayDrawer
                    position="end"
                    className={styles.chatSettingsDrawer}
                    open={isConfigPanelOpen}
                    modalType="non-modal"
                    onOpenChange={(_ev: DialogOpenChangeEvent, { open }: DialogOpenChangeData) => {
                        if (!open) setIsConfigPanelOpen(false);
                    }}
                >
                    <DrawerHeader>
                        <DrawerHeaderTitle
                            action={
                                <Button
                                    appearance="subtle"
                                    aria-label={t("labels.closeButton")}
                                    icon={<Dismiss24Regular />}
                                    onClick={() => setIsConfigPanelOpen(false)}
                                />
                            }
                        >
                            {t("labels.headerText")}
                        </DrawerHeaderTitle>
                    </DrawerHeader>
                    <DrawerBody>
                        <Settings
                            promptTemplate={promptTemplate}
                            temperature={temperature}
                            retrieveCount={retrieveCount}
                            agenticReasoningEffort={agenticReasoningEffort}
                            seed={seed}
                            minimumSearchScore={minimumSearchScore}
                            minimumRerankerScore={minimumRerankerScore}
                            useSemanticRanker={useSemanticRanker}
                            useSemanticCaptions={useSemanticCaptions}
                            useQueryRewriting={useQueryRewriting}
                            reasoningEffort={reasoningEffort}
                            excludeCategory={excludeCategory}
                            includeCategory={includeCategory}
                            retrievalMode={retrievalMode}
                            showMultimodalOptions={showMultimodalOptions}
                            sendTextSources={sendTextSources}
                            sendImageSources={sendImageSources}
                            searchTextEmbeddings={searchTextEmbeddings}
                            searchImageEmbeddings={searchImageEmbeddings}
                            showSemanticRankerOption={showSemanticRankerOption}
                            showQueryRewritingOption={showQueryRewritingOption}
                            showReasoningEffortOption={showReasoningEffortOption}
                            showVectorOption={showVectorOption}
                            useLogin={!!useLogin}
                            loggedIn={loggedIn}
                            requireAccessControl={requireAccessControl}
                            shouldStream={shouldStream}
                            streamingEnabled={streamingEnabled}
                            useSuggestFollowupQuestions={useSuggestFollowupQuestions}
                            showAgenticRetrievalOption={showAgenticRetrievalOption}
                            useAgenticKnowledgeBase={useAgenticKnowledgeBase}
                            useWebSource={webSourceEnabled}
                            showWebSourceOption={webSourceSupported}
                            useSharePointSource={sharePointSourceEnabled}
                            showSharePointSourceOption={sharePointSourceSupported}
                            hideMinimalRetrievalReasoningOption={hideMinimalRetrievalReasoningOption}
                            onChange={handleSettingsChange}
                        />
                        {useLogin && <TokenClaimsDisplay />}
                        <div style={{ marginTop: "auto", padding: "16px 0" }}>
                            <Button onClick={() => setIsConfigPanelOpen(false)}>{t("labels.closeButton")}</Button>
                        </div>
                    </DrawerBody>
                </OverlayDrawer>
            </div>
        </div>
    );
};

export default Chat;
