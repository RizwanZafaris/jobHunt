/**
 * ProfileNav — kept as a thin re-export for backwards compatibility.
 *
 * The new shared AppNav lives in '@/components/layout/AppNav' and is
 * mounted via AppShell. New code should import AppShell directly; this
 * shim keeps any lingering imports working.
 */
export { AppNav as default } from './layout/AppNav'
export { AppNav } from './layout/AppNav'
