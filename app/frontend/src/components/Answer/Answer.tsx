import { useMemo, useState, isValidElement, type ReactNode, type ComponentPropsWithoutRef } from "react";
import type { ExtraProps } from "react-markdown";
import { Button } from "@fluentui/react-components";
import { Copy24Regular, Checkmark24Regular, LightbulbFilament24Regular, ClipboardTextLtr24Regular } from "@fluentui/react-icons";
import { useTranslation } from "react-i18next";
import DOMPurify from "dompurify";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";

import styles from "./Answer.module.css";
import { ChatAppResponse, getCitationFilePath, SpeechConfig } from "../../api";
import { parseAnswerToHtml } from "./AnswerParser";
import { AnswerIcon } from "./AnswerIcon";
import { SpeechOutputBrowser } from "./SpeechOutputBrowser";
import { SpeechOutputAzure } from "./SpeechOutputAzure";

/** Collect plain text from rendered markdown nodes (order matches reading order). */
function collectPlainTextFromNodes(children: ReactNode, maxLen?: number): string {
    let out = "";
    const walk = (n: ReactNode): void => {
        if (maxLen !== undefined && out.length >= maxLen) return;
        if (n == null || typeof n === "boolean") return;
        if (typeof n === "string" || typeof n === "number") {
            out += String(n);
            return;
        }
        if (Array.isArray(n)) {
            n.forEach(walk);
            return;
        }
        if (isValidElement(n)) {
            const props = n.props as { children?: ReactNode };
            if (props.children != null) {
                walk(props.children);
            }
        }
    };
    walk(children);
    const t = out.trimStart();
    return maxLen !== undefined ? t.slice(0, maxLen) : t;
}

function collectPlainTextPrefix(children: ReactNode, maxLen = 160): string {
    return collectPlainTextFromNodes(children, maxLen);
}

/** Q1: / Q3A: / Q2 follow-up: verbatim script; optional leading `>` when the model prints blockquote style without a blank line. */
const APPLICANT_VERBATIM_QUESTION_BODY = /^\s*Q\d+(?:[A-Za-z]+(?:\s+[-\w]+)*|(?:\s+[-\w]+)+)?\s*:/;

/** OUTPUT A routing script: intern reads quoted text aloud (after optional `>`). */
const APPLICANT_VERBATIM_QUOTED_SCRIPT = /^\s*"/;

/** Markdown labels before the script (must match chat_answer.system.jinja2 OUTPUT A/B/C/E). */
/** Longer "Tell … (read verbatim)" before bare "Tell the applicant:" so the regex does not stop early. */
const APPLICANT_INSTRUCTION_LABEL_MD =
    "(?:\\*\\*Tell the applicant \\(read verbatim\\):\\*\\*|Tell the applicant \\(read verbatim\\):|\\*\\*Tell the applicant:\\*\\*|Tell the applicant:|\\*\\*Ask the applicant \\(read verbatim\\):\\*\\*|Ask the applicant \\(read verbatim\\):|\\*\\*Back to triage — ask the applicant \\(read verbatim\\):\\*\\*|Back to triage — ask the applicant \\(read verbatim\\):)";

/** Plain-text label alternation after ReactMarkdown resolves bold (for highlight detection). */
const PLAIN_APPLICANT_LABEL_ALT =
    "Tell the applicant \\(read verbatim\\):|Tell the applicant:|Ask the applicant \\(read verbatim\\):|Back to triage — ask the applicant \\(read verbatim\\):";

function applicantVerbatimBodyPrefix(children: ReactNode): string {
    let t = collectPlainTextPrefix(children);
    t = t.replace(new RegExp(`^\\s*(?:${PLAIN_APPLICANT_LABEL_ALT})\\s*>\\s*`, "i"), "");
    t = t.replace(new RegExp(`^\\s*(?:${PLAIN_APPLICANT_LABEL_ALT})\\s*`, "i"), "");
    t = t.replace(/^\s*>\s*/, "");
    return t.trimStart();
}

function shouldHighlightApplicantVerbatim(children: ReactNode): boolean {
    const body = applicantVerbatimBodyPrefix(children);
    if (APPLICANT_VERBATIM_QUESTION_BODY.test(body)) return true;
    if (APPLICANT_VERBATIM_QUOTED_SCRIPT.test(body)) return true;
    return false;
}

/** True when paragraph is only text (no links, emphasis, etc.) so we can split label vs script safely. */
function isPlainTextOnlyChildren(children: ReactNode): boolean {
    let ok = true;
    const walk = (n: ReactNode): void => {
        if (!ok) return;
        if (n == null || typeof n === "boolean") return;
        if (typeof n === "string" || typeof n === "number") return;
        if (Array.isArray(n)) {
            n.forEach(walk);
            return;
        }
        if (isValidElement(n)) {
            if (n.type === "br") return;
            ok = false;
        }
    };
    walk(children);
    return ok;
}

function trySplitApplicantLabelAndScript(full: string): { label: string; script: string } | null {
    const re = new RegExp(`^\\s*((?:${PLAIN_APPLICANT_LABEL_ALT})\\s*(?:>\\s*)?)(.*)$`, "is");
    const m = full.match(re);
    if (!m) return null;
    const rest = m[2].trimStart();
    if (!APPLICANT_VERBATIM_QUESTION_BODY.test(rest) && !APPLICANT_VERBATIM_QUOTED_SCRIPT.test(rest)) return null;
    const label = m[1].replace(/\s*>\s*$/u, "").trimEnd();
    return { label, script: rest };
}

/**
 * If the model compresses "Tell / Ask the applicant…" and the script onto one line, markdown keeps
 * one paragraph so the script is not blockquoted or highlighted. Insert a break and a blockquote
 * marker so the verbatim line matches OUTPUT A/B/C layout and ApplicantQuestion* styling applies.
 */
function ensureVerbatimBlockquoteNewline(markdown: string): string {
    const label = APPLICANT_INSTRUCTION_LABEL_MD;
    let s = markdown.replace(/\u00a0/g, " ");
    const h = "[ \\t\\u00a0]";
    s = s.replace(new RegExp(`(${label})${h}*(>)`, "g"), "$1\n\n$2");
    s = s.replace(new RegExp(`(${label})${h}*(")`, "g"), "$1\n\n> $2");
    s = s.replace(new RegExp(`(${label})${h}*(Q\\d)`, "g"), "$1\n\n> $2");
    return s;
}

type BlockquoteProps = ComponentPropsWithoutRef<"blockquote"> & Partial<ExtraProps>;

function ApplicantQuestionBlockquote({ children, className, ...rest }: BlockquoteProps) {
    const highlight = shouldHighlightApplicantVerbatim(children);
    const mergedClass = [className, highlight ? styles.applicantVerbatimQuestion : undefined].filter(Boolean).join(" ") || undefined;
    return (
        <blockquote {...rest} className={mergedClass}>
            {children}
        </blockquote>
    );
}

type ParagraphProps = ComponentPropsWithoutRef<"p"> & Partial<ExtraProps>;

function ApplicantQuestionParagraph({ children, className, ...rest }: ParagraphProps) {
    const fullText = collectPlainTextFromNodes(children);
    const labelScriptSplit = trySplitApplicantLabelAndScript(fullText);

    if (labelScriptSplit && isPlainTextOnlyChildren(children)) {
        return (
            <p {...rest} className={className}>
                <span className={styles.applicantInstructionLabel}>{labelScriptSplit.label}</span>
                <span className={styles.applicantVerbatimScript}>{labelScriptSplit.script}</span>
            </p>
        );
    }

    const highlight = shouldHighlightApplicantVerbatim(children);
    const mergedClass = [className, highlight ? styles.applicantVerbatimQuestion : undefined].filter(Boolean).join(" ") || undefined;
    return (
        <p {...rest} className={mergedClass}>
            {children}
        </p>
    );
}

const answerMarkdownComponents = {
    blockquote: ApplicantQuestionBlockquote,
    p: ApplicantQuestionParagraph
};

interface Props {
    answer: ChatAppResponse;
    index: number;
    speechConfig: SpeechConfig;
    isSelected?: boolean;
    isStreaming: boolean;
    onCitationClicked: (filePath: string) => void;
    onThoughtProcessClicked: () => void;
    onSupportingContentClicked: () => void;
    onFollowupQuestionClicked?: (question: string) => void;
    showFollowupQuestions?: boolean;
    showSpeechOutputBrowser?: boolean;
    showSpeechOutputAzure?: boolean;
}

export const Answer = ({
    answer,
    index,
    speechConfig,
    isSelected,
    isStreaming,
    onCitationClicked,
    onThoughtProcessClicked,
    onSupportingContentClicked,
    onFollowupQuestionClicked,
    showFollowupQuestions,
    showSpeechOutputAzure,
    showSpeechOutputBrowser
}: Props) => {
    const followupQuestions = answer.context?.followup_questions;
    const parsedAnswer = useMemo(() => parseAnswerToHtml(answer, isStreaming, onCitationClicked), [answer, isStreaming, onCitationClicked]);
    const { t } = useTranslation();
    const sanitizedAnswerHtml = DOMPurify.sanitize(parsedAnswer.answerHtml);
    const markdownForDisplay = useMemo(() => ensureVerbatimBlockquoteNewline(sanitizedAnswerHtml), [sanitizedAnswerHtml]);
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
        const tempElement = document.createElement("div");
        tempElement.innerHTML = sanitizedAnswerHtml;
        tempElement.querySelectorAll("sup").forEach(node => node.remove());
        tempElement.querySelectorAll(".citationStepBadge").forEach(node => node.remove());
        const textToCopy = tempElement.textContent ?? "";

        navigator.clipboard
            .writeText(textToCopy)
            .then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
            })
            .catch(err => console.error("Failed to copy text: ", err));
    };

    return (
        <div
            className={`${styles.answerContainer} ${isSelected ? styles.selected : ""}`}
            style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}
        >
            <div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <AnswerIcon />
                    <div>
                        <Button
                            appearance="transparent"
                            style={{ color: "black" }}
                            icon={copied ? <Checkmark24Regular /> : <Copy24Regular />}
                            title={copied ? t("tooltips.copied") : t("tooltips.copy")}
                            aria-label={copied ? t("tooltips.copied") : t("tooltips.copy")}
                            onClick={handleCopy}
                        />
                        <Button
                            appearance="transparent"
                            style={{ color: "black" }}
                            icon={<LightbulbFilament24Regular />}
                            title={t("tooltips.showThoughtProcess")}
                            aria-label={t("tooltips.showThoughtProcess")}
                            onClick={() => onThoughtProcessClicked()}
                            disabled={!answer.context.thoughts?.length || isStreaming}
                        />
                        <Button
                            appearance="transparent"
                            style={{ color: "black" }}
                            icon={<ClipboardTextLtr24Regular />}
                            title={t("tooltips.showSupportingContent")}
                            aria-label={t("tooltips.showSupportingContent")}
                            onClick={() => onSupportingContentClicked()}
                            disabled={!answer.context.data_points || isStreaming}
                        />
                        {showSpeechOutputAzure && (
                            <SpeechOutputAzure answer={sanitizedAnswerHtml} index={index} speechConfig={speechConfig} isStreaming={isStreaming} />
                        )}
                        {showSpeechOutputBrowser && <SpeechOutputBrowser answer={sanitizedAnswerHtml} />}
                    </div>
                </div>
            </div>

            <div style={{ flexGrow: 1 }}>
                <div className={styles.answerText}>
                    <ReactMarkdown
                        children={markdownForDisplay}
                        components={answerMarkdownComponents}
                        rehypePlugins={[rehypeRaw]}
                        remarkPlugins={[remarkGfm]}
                    />
                </div>
            </div>

            {!!parsedAnswer.citations.length && (
                <div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
                        <span className={styles.citationLearnMore}>{t("citationWithColon")}</span>
                        {parsedAnswer.citations.map(citation => {
                            const isWeb = citation.isWeb;
                            const displayIndex = citation.index;
                            const reference = citation.reference;
                            if (isWeb) {
                                // Attempt to find the matching web data point to retrieve its title
                                const webEntry = answer.context.data_points.external_results_metadata?.find(w => w.url === reference);
                                const titleOrUrl = webEntry?.title?.trim() ? webEntry.title : reference;
                                return (
                                    <span key={`${reference}-${displayIndex}`} className={styles.citationEntry}>
                                        <a className={styles.citation} title={reference} href={reference} target="_blank" rel="noopener noreferrer">
                                            {`${displayIndex}. ${titleOrUrl}`}
                                        </a>
                                    </span>
                                );
                            } else {
                                const path = getCitationFilePath(reference);
                                return (
                                    <span key={`${reference}-${displayIndex}`} className={styles.citationEntry}>
                                        <a
                                            className={styles.citation}
                                            title={reference}
                                            onClick={e => {
                                                e.preventDefault();
                                                onCitationClicked(path);
                                            }}
                                        >
                                            {`${displayIndex}. ${reference}`}
                                        </a>
                                    </span>
                                );
                            }
                        })}
                    </div>
                </div>
            )}

            {!!followupQuestions?.length && showFollowupQuestions && onFollowupQuestionClicked && (
                <div>
                    <div
                        style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}
                        className={`${!!parsedAnswer.citations.length ? styles.followupQuestionsList : ""}`}
                    >
                        <span className={styles.followupQuestionLearnMore}>{t("followupQuestions")}</span>
                        {followupQuestions.map((x, i) => {
                            return (
                                <a key={i} className={styles.followupQuestion} title={x} onClick={() => onFollowupQuestionClicked(x)}>
                                    {`${x}`}
                                </a>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
};
