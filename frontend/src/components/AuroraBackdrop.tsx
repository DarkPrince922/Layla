/** Decorative, compositor-animated light. Never captures clicks or scroll. */
export function AuroraBackdrop() {
  return <div className="aurora-backdrop" aria-hidden="true"><div className="aurora-light aurora-violet" /><div className="aurora-light aurora-mint" /><div className="aurora-grain" /></div>;
}
