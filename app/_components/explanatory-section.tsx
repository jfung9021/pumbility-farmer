import type { ReactNode } from "react";

type ExplanatorySectionProps = {
  title: string;
  children: ReactNode;
  className?: string;
};

export function ExplanatorySection({ title, children, className }: ExplanatorySectionProps) {
  return (
    <details className={["explanatory-section", className].filter(Boolean).join(" ")}>
      <summary>
        <span>{title}</span>
        <span aria-hidden="true" className="explanatory-section-indicator" />
      </summary>
      <div className="explanatory-section-content">{children}</div>
    </details>
  );
}
