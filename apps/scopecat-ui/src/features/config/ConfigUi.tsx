import { CircleDot } from "lucide-react";
import type { ReactNode } from "react";
import { classes } from "../../ui/styles";

export function ActionNote({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="mt-3.5 grid gap-1.5">
      <span className="text-[0.55rem] font-extrabold tracking-[0.07em] text-text-dim uppercase">
        审计备注
      </span>
      <textarea
        className="min-h-[58px] w-full resize-y rounded-[8px] border border-line bg-bg px-2.5 py-[9px] text-[0.68rem] text-text outline-0 focus:border-[rgb(128_163_207_/_45%)]"
        rows={2}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="为什么修改此配置？"
      />
    </label>
  );
}

export function ConfigFact({
  label,
  value,
  code = false,
}: {
  label: string;
  value: string;
  code?: boolean;
}) {
  return (
    <div className="grid min-w-0 gap-[5px]">
      <span className="text-[0.55rem] font-extrabold tracking-[0.07em] text-text-dim uppercase">
        {label}
      </span>
      {code ? (
        <code
          className="overflow-hidden text-[0.66rem] font-[650] text-ellipsis whitespace-nowrap text-text-soft"
          title={value}
        >
          {value}
        </code>
      ) : (
        <strong className="overflow-hidden text-[0.66rem] font-[650] text-ellipsis whitespace-nowrap text-text-soft">
          {value}
        </strong>
      )}
    </div>
  );
}

export function ConfigInlineEmpty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex items-start gap-[9px] rounded-[8px] border border-dashed border-line p-3 text-text-dim">
      <CircleDot className="mt-px flex-none" size={16} aria-hidden="true" />
      <div>
        <strong className="block text-[0.66rem] text-text-soft">{title}</strong>
        <p className="mt-1 mb-0 text-[0.6rem] leading-[1.45]">{detail}</p>
      </div>
    </div>
  );
}

export function ConfigBoundaryMessage({
  icon,
  title,
  detail,
  warning = false,
  compact = false,
  action,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  warning?: boolean;
  compact?: boolean;
  action?: ReactNode;
}) {
  return (
    <section
      className={classes(
        "grid min-h-[430px] place-content-center justify-items-center rounded-lg border border-line bg-panel px-[22px] py-[38px] text-center shadow-panel",
        compact && "min-h-[510px] border-0 bg-transparent shadow-none",
      )}
    >
      <span
        className={classes(
          "mb-[13px] grid size-12 place-items-center rounded-[12px] border border-line bg-panel-soft text-accent",
          warning && "border-[rgb(255_140_136_/_20%)] bg-red-soft text-red",
        )}
        aria-hidden="true"
      >
        {icon}
      </span>
      <h2 className="m-0 text-[0.95rem]">{title}</h2>
      <p className="mt-2 mb-[15px] max-w-[480px] text-[0.69rem] leading-[1.55] text-text-dim">
        {detail}
      </p>
      {action}
    </section>
  );
}
