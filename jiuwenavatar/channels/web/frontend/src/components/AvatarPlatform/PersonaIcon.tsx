/**
 * PersonaIcon — 分身模板 SVG 图标（替代 emoji）
 */

type PersonaIconKey = 'committer' | 'developer' | 'tester' | 'avatar' | string;

interface PersonaIconProps {
  icon: PersonaIconKey;
  size?: 'sm' | 'md';
}

function CommitterSvg() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
      <circle cx="10" cy="10" r="6" />
      <path d="M14.5 14.5L20 20" strokeLinecap="round" />
      <path d="M8 10h4M10 8v4" strokeLinecap="round" />
    </svg>
  );
}

function DeveloperSvg() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
      <path d="M8 9L4 12l4 3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M16 9l4 3-4 3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 7l-2 10" strokeLinecap="round" />
    </svg>
  );
}

function TesterSvg() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
      <path d="M12 3l8 4v6c0 4.5-3.5 7.5-8 8-4.5-.5-8-3.5-8-8V7l8-4z" />
      <path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DefaultAvatarSvg() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
      <circle cx="12" cy="8" r="4" />
      <path d="M5 20c0-3.5 3-6 7-6s7 2.5 7 6" strokeLinecap="round" />
    </svg>
  );
}

const ICON_MAP: Record<string, () => JSX.Element> = {
  committer: CommitterSvg,
  developer: DeveloperSvg,
  tester: TesterSvg,
  avatar: DefaultAvatarSvg,
};

const KNOWN_KEYS = new Set(['committer', 'developer', 'tester', 'avatar']);

export function PersonaIcon({ icon, size = 'md' }: PersonaIconProps) {
  const sizeClass = size === 'sm' ? 'persona-icon--sm' : 'persona-icon--md';
  if (icon?.startsWith('data:image/')) {
    return (
      <div className={`persona-icon ${sizeClass} persona-icon--image`}>
        <img src={icon} alt="" />
      </div>
    );
  }
  const key = icon && KNOWN_KEYS.has(icon) ? icon : 'avatar';
  const IconComponent = ICON_MAP[key] || DefaultAvatarSvg;
  const colorClass = ICON_MAP[key] ? `persona-icon--${key}` : 'persona-icon--avatar';

  return (
    <div className={`persona-icon ${sizeClass} ${colorClass}`}>
      <IconComponent />
    </div>
  );
}
