/** Old durable records predate this field and are verified by the server default. */
export interface ReceiptValidation {
  receipt_validation?: "verified" | "unverified" | "invalid";
  validation_reason?: string | null;
  validation_code?: string | null;
}
export function isVerifiedReceipt(value: ReceiptValidation) {
  return value.receipt_validation === undefined || value.receipt_validation === "verified";
}
export function ReceiptValidationNotice({ value }: { value: ReceiptValidation }) {
  if (isVerifiedReceipt(value)) return null;
  return (
    <div role="alert" className="text-sm text-yellow">
      <p>
        {value.receipt_validation === "invalid"
          ? "无效原件（契约不匹配）"
          : "未验证原件（验证未完成）"}
      </p>
      <p>
        可保存原件用于检查或保存后下载；保存不表示它是合格科学数据。不绘图、不参与比较，也不会重新采集。
      </p>
      {value.validation_reason && <p>{value.validation_reason}</p>}
      {value.validation_code && <p>原因代码：{value.validation_code}</p>}
    </div>
  );
}
export function OriginalReceiptDownload({ recordId }: { recordId: string }) {
  return (
    <a
      href={
        new URL(
          `/api/v1/direct-measurements/${encodeURIComponent(recordId)}/receipt`,
          globalThis.location?.origin ?? "http://localhost",
        ).href
      }
      download={`${recordId}.hardware-receipt`}
    >
      下载原始回执（非预览）
    </a>
  );
}
