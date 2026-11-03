export default function Logo({ size = 36 }) {
  return (
    <span className="logo" style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 24 24" width={size * 0.58} height={size * 0.58} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3v2M5 9a7 7 0 0 1 14 0v5a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3z" />
        <circle cx="9.5" cy="11.5" r=".8" fill="currentColor" />
        <circle cx="14.5" cy="11.5" r=".8" fill="currentColor" />
        <path d="M9 21h6" />
      </svg>
    </span>
  );
}
