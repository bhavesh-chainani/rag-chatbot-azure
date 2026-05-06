import { useMsal } from "@azure/msal-react";
import { getToken, useLogin } from "../../authConfig";
import { OverlayDrawer, DrawerHeader, DrawerHeaderTitle, DrawerBody, Spinner, Button, Input } from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { HistoryData, HistoryItem } from "../HistoryItem";
import { Answers, HistoryProviderOptions } from "../HistoryProviders/IProvider";
import { useHistoryManager, HistoryMetaData } from "../HistoryProviders";
import type { TFunction } from "i18next";
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

    const { t, i18n } = useTranslation();
    const groupedHistory = useMemo(
        () => groupHistoryByDate(filteredHistory, i18n.language, t),
        [filteredHistory, i18n.language, t]
    );
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
                {groupedHistory.map(({ key, label, items }) => (
                    <div key={key} className={styles.group}>
                        <p className={styles.groupLabel}>{label}</p>
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

type HistoryDateGroup = { key: string; label: string; items: HistoryData[] };

function startOfLocalDay(d: Date): Date {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x;
}

function isoLocalDate(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
}

function dayKeyToSortNumber(ymd: string): number {
    return parseInt(ymd.replace(/-/g, ""), 10);
}

function monthKeyToSortNumber(ym: string): number {
    return parseInt(ym.replace(/-/g, ""), 10);
}

/** Newest sections first: Today, Yesterday, then each calendar day in the rolling 7-day window, then month buckets for older chats. */
function compareHistoryGroupKeys(a: string, b: string): number {
    const weight = (k: string): number => {
        if (k === "__today__") return Number.MAX_SAFE_INTEGER;
        if (k === "__yesterday__") return Number.MAX_SAFE_INTEGER - 1;
        if (k.startsWith("day:")) return dayKeyToSortNumber(k.slice(4));
        if (k.startsWith("month:")) return monthKeyToSortNumber(k.slice(6));
        return 0;
    };
    return weight(b) - weight(a);
}

function formatHistorySectionDate(locale: string, d: Date): string {
    return d.toLocaleDateString(locale, { month: "long", day: "numeric", year: "numeric" });
}

function labelForHistoryGroupKey(key: string, locale: string, t: TFunction): string {
    if (key === "__today__") {
        const d = startOfLocalDay(new Date());
        return `${t("history.today")} (${formatHistorySectionDate(locale, d)})`;
    }
    if (key === "__yesterday__") {
        const d = startOfLocalDay(new Date());
        d.setDate(d.getDate() - 1);
        return `${t("history.yesterday")} (${formatHistorySectionDate(locale, d)})`;
    }
    if (key.startsWith("day:")) {
        const [y, m, d] = key.slice(4).split("-").map(Number);
        const date = new Date(y, m - 1, d, 12, 0, 0, 0);
        const weekday = date.toLocaleDateString(locale, { weekday: "long" });
        return `${weekday} (${formatHistorySectionDate(locale, date)})`;
    }
    if (key.startsWith("month:")) {
        const [y, m] = key.slice(6).split("-").map(Number);
        const date = new Date(y, m - 1, 1, 12, 0, 0, 0);
        return date.toLocaleDateString(locale, { month: "long", year: "numeric" });
    }
    return key;
}

function groupHistoryByDate(items: HistoryData[], locale: string, t: TFunction): HistoryDateGroup[] {
    const today = startOfLocalDay(new Date());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const windowStart = new Date(today);
    windowStart.setDate(windowStart.getDate() - 6);

    const buckets = new Map<string, HistoryData[]>();

    for (const item of items) {
        const itemDay = startOfLocalDay(new Date(item.timestamp));
        let key: string;
        if (itemDay.getTime() === today.getTime()) {
            key = "__today__";
        } else if (itemDay.getTime() === yesterday.getTime()) {
            key = "__yesterday__";
        } else if (itemDay.getTime() >= windowStart.getTime()) {
            key = `day:${isoLocalDate(itemDay)}`;
        } else {
            const y = itemDay.getFullYear();
            const m = String(itemDay.getMonth() + 1).padStart(2, "0");
            key = `month:${y}-${m}`;
        }
        const arr = buckets.get(key) ?? [];
        arr.push(item);
        buckets.set(key, arr);
    }

    for (const arr of buckets.values()) {
        arr.sort((a, b) => b.timestamp - a.timestamp);
    }

    const keys = Array.from(buckets.keys());
    keys.sort(compareHistoryGroupKeys);

    return keys.map(key => ({
        key,
        label: labelForHistoryGroupKey(key, locale, t),
        items: buckets.get(key)!
    }));
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
