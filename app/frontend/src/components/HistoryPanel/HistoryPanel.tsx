import { useMsal } from "@azure/msal-react";
import { getToken, useLogin } from "../../authConfig";
import { OverlayDrawer, DrawerHeader, DrawerHeaderTitle, DrawerBody, Spinner, Button, Input } from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { HistoryData, HistoryItem } from "../HistoryItem";
import { Answers, HistoryProviderOptions } from "../HistoryProviders/IProvider";
import { useHistoryManager, HistoryMetaData } from "../HistoryProviders";
import { useTranslation } from "react-i18next";
import styles from "./HistoryPanel.module.css";

const HISTORY_COUNT_PER_LOAD = 20;
const HISTORY_SEARCH_COUNT = 200;

export const HistoryPanel = ({
    provider,
    isOpen,
    notify,
    onClose,
    onChatSelected
}: {
    provider: HistoryProviderOptions;
    isOpen: boolean;
    notify: boolean;
    onClose: () => void;
    onChatSelected: (answers: Answers) => void;
}) => {
    const historyManager = useHistoryManager(provider);
    const [history, setHistory] = useState<HistoryMetaData[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [hasMoreHistory, setHasMoreHistory] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const isLoadingRef = useRef(false);

    const client = useLogin ? useMsal().instance : undefined;

    useEffect(() => {
        if (!isOpen) return;
        if (notify) {
            setHistory([]);
            historyManager.resetContinuationToken();
            setHasMoreHistory(true);
        }
    }, [isOpen, notify]);

    const loadMoreHistory = async () => {
        if (isLoadingRef.current) {
            return;
        }
        isLoadingRef.current = true;
        setIsLoading(() => true);
        try {
            const token = client ? await getToken(client) : undefined;
            const items = await historyManager.getNextItems(HISTORY_COUNT_PER_LOAD, token);
            if (items.length === 0) {
                setHasMoreHistory(false);
            }
            setHistory(prevHistory => [...prevHistory, ...items]);
        } finally {
            isLoadingRef.current = false;
            setIsLoading(() => false);
        }
    };

    const handleSelect = async (id: string) => {
        const token = client ? await getToken(client) : undefined;
        const item = await historyManager.getItem(id, token);
        if (item) {
            onChatSelected(item);
        }
    };

    const handleDelete = async (id: string) => {
        const token = client ? await getToken(client) : undefined;
        await historyManager.deleteItem(id, token);
        setHistory(prevHistory => prevHistory.filter(item => item.id !== id));
    };

    const filteredHistory = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) {
            return history;
        }
        return history;
    }, [history, searchQuery]);

    const groupedHistory = useMemo(() => groupHistory(filteredHistory), [filteredHistory]);
    const isSearching = searchQuery.trim().length > 0;

    useEffect(() => {
        if (!isOpen) return;

        const query = searchQuery.trim();
        if (!query) return;

        const timeoutId = setTimeout(async () => {
            setIsLoading(true);
            try {
                const token = client ? await getToken(client) : undefined;
                const items = await historyManager.searchItems(query, HISTORY_SEARCH_COUNT, token);
                setHistory(items);
                setHasMoreHistory(false);
            } finally {
                setIsLoading(false);
            }
        }, 250);

        return () => clearTimeout(timeoutId);
    }, [searchQuery, isOpen]);

    useEffect(() => {
        if (!isOpen) return;
        if (searchQuery.trim()) return;
        if (history.length > 0) return;
        if (isLoadingRef.current) return;
        void loadMoreHistory();
    }, [isOpen, searchQuery, history.length]);

    const { t } = useTranslation();

    const handleClose = () => {
        setHistory([]);
        setSearchQuery("");
        setHasMoreHistory(true);
        historyManager.resetContinuationToken();
        onClose();
    };

    return (
        <OverlayDrawer
            position="start"
            className={styles.drawer}
            style={{ width: "320px" }}
            modalType="non-modal"
            open={isOpen}
            onOpenChange={(_ev: any, { open }: { open: boolean }) => {
                if (!open) {
                    handleClose();
                }
            }}
        >
            <DrawerHeader className={styles.drawerHeader}>
                <DrawerHeaderTitle
                    className={styles.drawerHeaderTitle}
                    action={
                        <Button
                            className={styles.drawerCloseButton}
                            appearance="subtle"
                            aria-label={t("labels.closeButton")}
                            icon={<Dismiss24Regular />}
                            onClick={handleClose}
                        />
                    }
                >
                    {t("history.chatHistory")}
                </DrawerHeaderTitle>
            </DrawerHeader>
            <DrawerBody className={styles.drawerBody}>
                <div className={styles.searchContainer}>
                    <Input
                        className={styles.searchInput}
                        value={searchQuery}
                        onChange={(_e, data) => setSearchQuery(data.value)}
                        placeholder={t("history.searchPlaceholder")}
                        aria-label={t("history.searchPlaceholder")}
                    />
                </div>
                {Object.entries(groupedHistory).map(([group, items]) => (
                    <div key={group} className={styles.group}>
                        <p className={styles.groupLabel}>{t(group)}</p>
                        <ul className={styles.chatList} role="list">
                            {items.map(item => (
                                <li key={item.id} className={styles.chatListItem}>
                                    <HistoryItem item={item} onSelect={handleSelect} onDelete={handleDelete} />
                                </li>
                            ))}
                        </ul>
                    </div>
                ))}
                {isLoading && <Spinner className={styles.spinner} />}
                {history.length === 0 && !isLoading && <p className={styles.emptyState}>{t("history.noHistory")}</p>}
                {history.length > 0 && filteredHistory.length === 0 && !isLoading && (
                    <p className={styles.emptySearch}>No matching chats</p>
                )}
                {hasMoreHistory && !isLoading && !isSearching && <InfiniteLoadingButton func={loadMoreHistory} />}
            </DrawerBody>
        </OverlayDrawer>
    );
};

function groupHistory(history: HistoryData[]) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const lastWeek = new Date(today);
    lastWeek.setDate(lastWeek.getDate() - 7);
    const lastMonth = new Date(today);
    lastMonth.setDate(lastMonth.getDate() - 30);

    return history.reduce(
        (groups, item) => {
            const itemDate = new Date(item.timestamp);
            let group;

            if (itemDate >= today) {
                group = "history.today";
            } else if (itemDate >= yesterday) {
                group = "history.yesterday";
            } else if (itemDate >= lastWeek) {
                group = "history.last7days";
            } else if (itemDate >= lastMonth) {
                group = "history.last30days";
            } else {
                group = itemDate.toLocaleDateString(undefined, { year: "numeric", month: "long" });
            }

            if (!groups[group]) {
                groups[group] = [];
            }
            groups[group].push(item);
            return groups;
        },
        {} as Record<string, HistoryData[]>
    );
}

const InfiniteLoadingButton = ({ func }: { func: () => void }) => {
    const buttonRef = useRef(null);

    useEffect(() => {
        const observer = new IntersectionObserver(
            entries => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        if (buttonRef.current) {
                            func();
                        }
                    }
                });
            },
            {
                root: null,
                threshold: 0
            }
        );

        if (buttonRef.current) {
            observer.observe(buttonRef.current);
        }

        return () => {
            if (buttonRef.current) {
                observer.unobserve(buttonRef.current);
            }
        };
    }, []);

    return <button ref={buttonRef} onClick={func} />;
};
