import type { MeasurementTableModel } from "./measurement-visualization";

export function MeasurementRecordTable({ table }: { table: MeasurementTableModel }) {
  return (
    <div className="overflow-x-auto" data-testid="measurement-table">
      <table className="w-full min-w-max border-collapse text-left text-[0.66rem]">
        <thead className="bg-panel-soft text-[0.59rem] tracking-[0.06em] text-text-dim uppercase">
          <tr>
            {table.columns.map((column) => (
              <th className="border-b border-line px-3 py-2 font-bold" key={column.id} scope="col">
                {column.label}
                {column.role !== "point" && (
                  <span className="ml-1.5 text-[0.52rem] text-text-dim">{column.role}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr className="border-b border-line last:border-b-0" key={row.id}>
              {row.cells.map((cell, index) =>
                index === 0 ? (
                  <th
                    className="px-3 py-2 font-mono font-medium text-text-soft"
                    key={index}
                    scope="row"
                  >
                    {cell}
                  </th>
                ) : (
                  <td
                    className="max-w-[260px] truncate px-3 py-2 text-text-soft"
                    key={index}
                    title={cell}
                  >
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
