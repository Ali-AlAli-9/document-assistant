import React from 'react'

/**
 * Line icons drawn inline (24x24, currentColor).
 *
 * Inline SVG instead of emoji: identical rendering on every platform and OS,
 * inherits the surrounding text color, and scales with font-size — no emoji
 * font, no platform-dependent glyph substitution.
 */
function Svg({ size = 18, children, ...rest }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

export function IconUser(props) {
  return (
    <Svg {...props}>
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </Svg>
  )
}

export function IconDocument(props) {
  return (
    <Svg {...props}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </Svg>
  )
}

export function IconCloud(props) {
  return (
    <Svg {...props}>
      <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
    </Svg>
  )
}

export function IconMonitor(props) {
  return (
    <Svg {...props}>
      <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
      <line x1="8" y1="21" x2="16" y2="21" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </Svg>
  )
}

export function IconCheck(props) {
  return (
    <Svg {...props}>
      <polyline points="20 6 9 17 4 12" />
    </Svg>
  )
}

export function IconCheckCircle(props) {
  return (
    <Svg {...props}>
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </Svg>
  )
}

// Filled body + open shackle + a punched keyhole, so the lock reads as a
// solid badge rather than a generic outline glyph (it would otherwise sit
// next to IconClock at the same weight and read as the same idea).
export function IconLock(props) {
  return (
    <Svg {...props}>
      <path d="M7.75 10.5V7a4.25 4.25 0 0 1 8.5 0v3.5" />
      <rect x="3.5" y="10.5" width="17" height="11" rx="3" fill="currentColor" stroke="none" />
      <circle cx="12" cy="15.2" r="1.7" className="lock-keyhole" stroke="none" />
      <path d="M11.2 16.6h1.6l.45 2.1h-2.5z" className="lock-keyhole" stroke="none" />
    </Svg>
  )
}

export function IconClock(props) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15.5 14" />
    </Svg>
  )
}
