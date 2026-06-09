import { useEffect, useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import styles from "./HistoryItem.module.css";
import { Button, Input } from "@fluentui/react-components";
import { Delete24Regular, Edit24Regular } from "@fluentui/react-icons";

export interface HistoryData {
    id: string;
    title: string;
    timestamp: number;
}

interface HistoryItemProps {
    item: HistoryData;
    onSelect: (id: string) => void;
    onDelete: (id: string) => void;
    onRename: (id: string, title: string) => Promise<void>;
}

const MAX_CHAT_TITLE_LENGTH = 120;

export function HistoryItem({ item, onSelect, onDelete, onRename }: HistoryItemProps) {
    const { t } = useTranslation();
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [draftTitle, setDraftTitle] = useState(item.title);
    const [isSaving, setIsSaving] = useState(false);
    const [renameError, setRenameError] = useState<string | undefined>();
    const inputRef = useRef<HTMLInputElement | null>(null);

    useEffect(() => {
        setDraftTitle(item.title);
        setRenameError(undefined);
    }, [item.title]);

    useEffect(() => {
        if (isEditing) {
            inputRef.current?.focus();
            inputRef.current?.select();
        }
    }, [isEditing]);

    const handleDelete = useCallback(() => {
        setIsModalOpen(false);
        onDelete(item.id);
    }, [item.id, onDelete]);

    const startEditing = useCallback(() => {
        setDraftTitle(item.title);
        setRenameError(undefined);
        setIsEditing(true);
    }, [item.title]);

    const cancelEditing = useCallback(() => {
        setDraftTitle(item.title);
        setRenameError(undefined);
        setIsEditing(false);
    }, [item.title]);

    const saveTitle = useCallback(async () => {
        const trimmedTitle = draftTitle.trim();
        if (!trimmedTitle) {
            setRenameError(t("history.renameValidation"));
            return;
        }

        if (trimmedTitle === item.title) {
            setIsEditing(false);
            setRenameError(undefined);
            return;
        }

        try {
            setIsSaving(true);
            setRenameError(undefined);
            await onRename(item.id, trimmedTitle);
            setIsEditing(false);
        } catch (error) {
            console.error("Failed to rename chat history:", error);
            setRenameError(t("history.renameError"));
        } finally {
            setIsSaving(false);
        }
    }, [draftTitle, item.id, item.title, onRename, t]);

    const handleEditKeyDown = useCallback(
        async (event: React.KeyboardEvent<HTMLInputElement>) => {
            if (event.key === "Enter") {
                event.preventDefault();
                await saveTitle();
            }
            if (event.key === "Escape") {
                event.preventDefault();
                cancelEditing();
            }
        },
        [cancelEditing, saveTitle]
    );

    return (
        <div className={styles.historyItem}>
            <div className={styles.historyItemMain}>
                {isEditing ? (
                    <>
                        <Input
                            ref={inputRef}
                            className={styles.renameInput}
                            value={draftTitle}
                            onChange={(_ev, data) => setDraftTitle(data.value.slice(0, MAX_CHAT_TITLE_LENGTH))}
                            onKeyDown={handleEditKeyDown}
                            aria-label={t("history.renameAriaLabel")}
                            placeholder={t("history.renamePlaceholder")}
                            disabled={isSaving}
                        />
                        {renameError && <div className={styles.renameError}>{renameError}</div>}
                        <div className={styles.renameActions}>
                            <Button size="small" onClick={cancelEditing} disabled={isSaving}>
                                {t("history.cancelLabel")}
                            </Button>
                            <Button size="small" appearance="primary" onClick={() => void saveTitle()} disabled={isSaving}>
                                {t("history.saveLabel")}
                            </Button>
                        </div>
                    </>
                ) : (
                    <button onClick={() => onSelect(item.id)} className={styles.historyItemButton}>
                        <div className={styles.historyItemTitle}>{item.title}</div>
                    </button>
                )}
            </div>
            {!isEditing && (
                <div className={styles.actionButtons}>
                    <button
                        onClick={startEditing}
                        className={styles.iconButton}
                        aria-label={t("history.renameLabel")}
                        title={t("history.renameLabel")}
                    >
                        <Edit24Regular className={styles.actionIcon} />
                    </button>
                    <button
                        onClick={() => setIsModalOpen(true)}
                        className={styles.iconButton}
                        aria-label={t("history.deleteAriaLabel")}
                        title={t("history.deleteLabel")}
                    >
                        <Delete24Regular className={styles.actionIcon} />
                    </button>
                </div>
            )}
            <DeleteHistoryModal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)} onConfirm={handleDelete} />
        </div>
    );
}

function DeleteHistoryModal({ isOpen, onClose, onConfirm }: { isOpen: boolean; onClose: () => void; onConfirm: () => void }) {
    if (!isOpen) return null;
    const { t } = useTranslation();
    return (
        <div className={styles.modalOverlay}>
            <div className={styles.modalContent}>
                <h2 className={styles.modalTitle}>{t("history.deleteModalTitle")}</h2>
                <p className={styles.modalDescription}>{t("history.deleteModalDescription")}</p>
                <div className={styles.modalActions}>
                    <Button onClick={onClose} className={styles.modalCancelButton}>
                        {t("history.cancelLabel")}
                    </Button>
                    <Button onClick={onConfirm} className={styles.modalConfirmButton}>
                        {t("history.deleteLabel")}
                    </Button>
                </div>
            </div>
        </div>
    );
}
