/**
 * Icon — inline SVG icon system.
 *
 * Replaces emoji-as-icon throughout the app. Uses Phosphor-style stroke
 * icons at consistent 1.5px stroke, currentColor fill so they inherit
 * text color through tokens.
 *
 * Add new icons by extending the PATHS map. Always render with role
 * "img" + an aria-label OR aria-hidden="true" if purely decorative.
 */
import { SVGProps } from 'react'

export type IconName =
  | 'search'
  | 'star'
  | 'mail'
  | 'building'
  | 'target'
  | 'rocket'
  | 'moon'
  | 'sun'
  | 'refresh'
  | 'sparkles'
  | 'download'
  | 'plus'
  | 'minus'
  | 'check'
  | 'alert-triangle'
  | 'info'
  | 'x'
  | 'eye'
  | 'menu'
  | 'pencil'
  | 'arrow-right'
  | 'arrow-up-right'
  | 'chevron-down'
  | 'chevron-up'
  | 'chevron-right'
  | 'document'
  | 'sliders'
  | 'brain'
  | 'currency'
  | 'coffee'
  | 'play'
  | 'briefcase'
  | 'note'
  | 'graph'
  | 'link'
  | 'shield-check'
  | 'shield-warning'
  | 'circle-half'
  | 'tag'
  | 'users'
  | 'clipboard-list'
  | 'bar-chart-3'
  | 'image'
  | 'copy'
  | 'map-pin'
  | 'external-link'

const PATHS: Record<IconName, React.ReactNode> = {
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  star: <path d="M12 3.5l2.6 5.4 5.9.9-4.3 4.1 1 5.9L12 17l-5.3 2.8 1-5.9-4.3-4.1 5.9-.9z" />,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></>,
  building: <><rect x="4" y="3" width="16" height="18" rx="1" /><path d="M9 7h.01M15 7h.01M9 11h.01M15 11h.01M9 15h.01M15 15h.01M10 21v-3h4v3" /></>,
  target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.2" /></>,
  rocket: <path d="M14 6c4-1 5 1 5 1s-1 4-5 5l-2 4-3-3 4-2c0-4 1-5 1-5zM6 17c0 0 0-2 1-3s3-1 3-1l3 3s-1 2-2 3-3 1-3 1-2-1-2-3z" />,
  moon: <path d="M21 12.8A8.5 8.5 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 3v1.5M12 19.5V21M3 12h1.5M19.5 12H21M5.6 5.6l1 1M17.4 17.4l1 1M5.6 18.4l1-1M17.4 6.6l1-1" /></>,
  refresh: <path d="M21 12a9 9 0 1 1-3.6-7.2M21 4v5h-5" />,
  sparkles: <path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6zM19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8z" />,
  download: <path d="M12 4v12m0 0-4-4m4 4 4-4M5 20h14" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  check: <path d="m5 12 5 5L20 7" />,
  'alert-triangle': <path d="M12 4 2.5 20h19zM12 10v4M12 17h.01" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v5h1" /></>,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  pencil: <path d="M4 20h4l11-11-4-4L4 16zM14 6l4 4" />,
  'arrow-right': <path d="M5 12h14m-5-5 5 5-5 5" />,
  'arrow-up-right': <path d="M7 17 17 7M9 7h8v8" />,
  'chevron-down': <path d="m6 9 6 6 6-6" />,
  'chevron-up': <path d="m6 15 6-6 6 6" />,
  'chevron-right': <path d="m9 6 6 6-6 6" />,
  document: <path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8zM14 3v5h5M9 13h6M9 17h4" />,
  sliders: <path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h-4M14 4v4M8 10v4M16 16v4" />,
  brain: <path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-3 3 3 3 0 0 0 1 2.2A3 3 0 0 0 5 17a3 3 0 0 0 4 3v-16zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 3 3 3 3 0 0 1-1 2.2A3 3 0 0 1 19 17a3 3 0 0 1-4 3" />,
  currency: <path d="M12 2v20M16 6c0-2-2-3-4-3s-4 1-4 3 2 3 4 3 4 1 4 3-2 3-4 3-4-1-4-3" />,
  coffee: <path d="M4 8h14v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4zM18 8h2a2 2 0 0 1 0 4h-2M6 4v2M10 4v2M14 4v2" />,
  play: <path d="m7 4 12 8-12 8z" />,
  briefcase: <><rect x="3" y="7" width="18" height="13" rx="1" /><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18" /></>,
  note: <path d="M5 4h14v16l-7-3-7 3z" />,
  graph: <path d="M4 20V4M4 20h16M8 16V12M12 16V8M16 16v-6" />,
  link: <path d="M10 14a4 4 0 0 0 5.6 0l3-3a4 4 0 0 0-5.6-5.6l-1 1M14 10a4 4 0 0 0-5.6 0l-3 3a4 4 0 0 0 5.6 5.6l1-1" />,
  'shield-check': <path d="M12 3 4 6v6c0 4 3 7.5 8 9 5-1.5 8-5 8-9V6zM9 12l2 2 4-4" />,
  'shield-warning': <path d="M12 3 4 6v6c0 4 3 7.5 8 9 5-1.5 8-5 8-9V6zM12 8v4M12 16h.01" />,
  'circle-half': <><circle cx="12" cy="12" r="9" /><path d="M12 3v18" /></>,
  tag: <path d="M3 12V4h8l10 10-8 8z M7 8h.01" />,
  users: <><circle cx="9" cy="8" r="3" /><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" /><circle cx="17" cy="9" r="2.5" /><path d="M15 14c1.5-.7 3-1 4-1 2.2 0 4 1.8 4 4" /></>,
  'clipboard-list': <><rect x="6" y="4" width="12" height="17" rx="1" /><path d="M9 4h6v3H9zM10 11h6M10 15h6M10 19h4" /></>,
  'bar-chart-3': <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  image: <><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="9" cy="11" r="2" /><path d="m3 17 5-5 4 4 3-3 6 6" /></>,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></>,
  'map-pin': <><path d="M12 21s-6-5.3-6-10a6 6 0 1 1 12 0c0 4.7-6 10-6 10z" /><circle cx="12" cy="11" r="2" /></>,
  'external-link': <><path d="M15 3h6v6" /><path d="M21 3l-9 9" /><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" /></>,
}

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName
  size?: number
  /** When true, the icon is decorative — not announced by AT. */
  decorative?: boolean
  /** When the icon carries meaning, provide a label. */
  label?: string
}

export function Icon({
  name,
  size = 16,
  decorative = true,
  label,
  className = '',
  ...props
}: IconProps) {
  const ariaProps = decorative
    ? { 'aria-hidden': true as const, focusable: false as const }
    : { role: 'img' as const, 'aria-label': label }
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
      {...ariaProps}
      {...props}
    >
      {PATHS[name]}
    </svg>
  )
}

export default Icon
