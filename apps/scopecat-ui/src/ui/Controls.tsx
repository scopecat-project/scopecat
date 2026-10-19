import type { ComponentProps } from "react";
import {
  classes,
  fieldLabel,
  formControl,
  primaryButton,
  dangerButton,
  secondaryButton,
  errorNotice,
  notice,
} from "./styles";

// Native controls keep platform keyboard behavior and form submission semantics.
// Base UI remains the foundation for composite widgets and dialogs.
export function Button({
  variant = "secondary",
  className,
  ...props
}: ComponentProps<"button"> & { variant?: "primary" | "secondary" | "danger" }) {
  return (
    <button
      {...props}
      className={classes(
        variant === "primary"
          ? primaryButton
          : variant === "danger"
            ? dangerButton
            : secondaryButton,
        className,
      )}
    />
  );
}
export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input {...props} className={classes(formControl, className)} />;
}
export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select {...props} className={classes(formControl, className)} />;
}
export function Field({ className, children, ...props }: ComponentProps<"label">) {
  return (
    <label {...props} className={classes(fieldLabel, className)}>
      {children}
    </label>
  );
}
// A wrapping action row, without ARIA toolbar semantics (no roving tabindex).
export function ActionRow({ className, ...props }: ComponentProps<"div">) {
  return (
    <div {...props} className={classes("flex min-w-0 flex-wrap items-end gap-3", className)} />
  );
}
export function Notice({ role = "status", className, ...props }: ComponentProps<"p">) {
  return (
    <p
      {...props}
      role={role}
      className={classes(role === "alert" ? errorNotice : notice, className)}
    />
  );
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea {...props} className={classes(formControl, "min-h-20", className)} />;
}
