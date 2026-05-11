import { useTranslation } from "react-i18next";

import styles from "./UserChatMessage.module.css";

function isSameLocalCalendarDay(aMs: number, bMs: number): boolean {
    const a = new Date(aMs);
    const b = new Date(bMs);
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Visible label: time-only for “today”; date + time for older sends (e.g. follow-ups the next day). */
function formatSentAtLabel(sentAtMs: number, language: string, nowMs: number = Date.now()): string {
    const d = new Date(sentAtMs);
    if (isSameLocalCalendarDay(sentAtMs, nowMs)) {
        return d.toLocaleTimeString(language, { hour: "numeric", minute: "2-digit" });
    }
    const now = new Date(nowMs);
    const opts: Intl.DateTimeFormatOptions = {
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit"
    };
    if (d.getFullYear() !== now.getFullYear()) {
        opts.year = "numeric";
    }
    return d.toLocaleString(language, opts);
}

interface Props {
    message: string;
    /** Epoch ms when the user sent this message; shown as a small local timestamp. */
    sentAt?: number;
}

export const UserChatMessage = ({ message, sentAt }: Props) => {
    const { i18n } = useTranslation();
    const timeLabel = sentAt != null ? formatSentAtLabel(sentAt, i18n.language) : null;
    const timeTitle =
        sentAt != null
            ? new Date(sentAt).toLocaleString(i18n.language, {
                  dateStyle: "medium",
                  timeStyle: "short"
              })
            : undefined;

    return (
        <div className={styles.container}>
            <div className={styles.bubbleColumn}>
                <div className={styles.message}>{message}</div>
                {timeLabel != null && sentAt != null ? (
                    <time className={styles.timestamp} dateTime={new Date(sentAt).toISOString()} title={timeTitle}>
                        {timeLabel}
                    </time>
                ) : null}
            </div>
        </div>
    );
};
