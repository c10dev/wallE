import type * as React from 'react';
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> { variant?: 'primary' | 'secondary' | 'ghost' }
export declare function Button(props: ButtonProps): React.ReactElement;
export interface BadgeProps { tone?: 'neutral' | 'walle' | 'eve' | 'leaf' | 'trim'; className?: string; children?: React.ReactNode }
export declare function Badge(props: BadgeProps): React.ReactElement;
export interface JudgeRef { file: string; line: number; text?: string }
export interface JudgeVerdict { real: boolean; keep: 'a' | 'b' | 'both' | 'neither' | 'ask'; fix: string; why?: string }
export interface JudgeAnswerProps { id?: string; type?: 'conflict' | 'cost'; pair?: string; refs: JudgeRef[]; verdict?: JudgeVerdict | null; judge?: string; cached?: boolean; compact?: boolean }
export declare function JudgeAnswer(props: JudgeAnswerProps): React.ReactElement;
export interface CommandBarProject { path: string; branch?: string }
export interface CommandBarDevice { name: string; status: 'online' | 'busy' | 'offline'; detail?: string }
export interface CommandBarProps { project?: CommandBarProject; device?: CommandBarDevice; agent?: string; defaultValue?: string; placeholder?: string; running?: boolean; clearOnRun?: boolean; hint?: React.ReactNode; onRun?: (command: string) => void }
export declare function CommandBar(props: CommandBarProps): React.ReactElement;
export interface SidebarItem { id: string; title: string; group?: string; meta?: React.ReactNode }
export interface SidebarProps { items?: SidebarItem[]; activeId?: string; onSelect?: (id: string) => void; actionLabel?: string; onAction?: () => void; label?: string; footer?: React.ReactNode }
export declare function Sidebar(props: SidebarProps): React.ReactElement;
export interface Suggestion { kicker?: string; text: string; command?: string }
export interface EmptyStateProps { title?: string; body?: React.ReactNode; suggestions?: Suggestion[]; onPick?: (s: Suggestion) => void; note?: React.ReactNode; children?: React.ReactNode }
export declare function EmptyState(props: EmptyStateProps): React.ReactElement;
/** One source row of `first_call.sources` in `walle context --json`. Other fields are kept and ignored. */
export interface WalleContextSource { source: string; label?: string; items?: number; tokens_est: number }
/** The object `walle context --json` prints (or a run's context.json). */
export interface WalleContextRun { run: { agent: 'claude' | 'codex'; estimator?: string }; note?: string; session?: { cwd?: string; model?: string; start?: string; entrypoint?: string; claude_code_version?: string }; first_call?: { measured: { total: number; [k: string]: number }; sources: WalleContextSource[]; estimated_total?: number; residual?: { tokens: number } } }
/** The object `walle context --usage --json` prints. */
export interface WalleUsageRun { run: { agent: 'claude' | 'codex' }; usage: { sessions: number; since?: string | null; before?: string | null; window?: { first?: string; last?: string }; servers?: { server: string; key?: string; sessions_loaded: number; sessions_used: number; tokens_unused_est?: number; used_in?: [string, number][] }[]; skills?: { skill: string; sessions_listed: number; sessions_used: number }[] } }
export interface ContextPanelProps { context?: WalleContextRun; usage?: WalleUsageRun; window?: number; limit?: number; note?: React.ReactNode }
export declare function ContextPanel(props: ContextPanelProps): React.ReactElement;
export type ScanStep = 'collect' | 'stack' | 'classify' | 'pairs' | 'triage';
export interface ScanProgressProps { step?: ScanStep | number; done?: boolean; roots?: string[]; counts?: { files?: number; pairs?: number; items?: number }; elapsed?: number; onCancel?: () => void; children?: React.ReactNode }
export declare function ScanProgress(props: ScanProgressProps): React.ReactElement;
declare global { interface Window { WallE: { Button: typeof Button; Badge: typeof Badge; JudgeAnswer: typeof JudgeAnswer; CommandBar: typeof CommandBar; Sidebar: typeof Sidebar; EmptyState: typeof EmptyState; ContextPanel: typeof ContextPanel; ScanProgress: typeof ScanProgress } } }
