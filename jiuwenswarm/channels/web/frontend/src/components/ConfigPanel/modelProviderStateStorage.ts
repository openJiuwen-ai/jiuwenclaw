import type { ModelEntry } from "../../types";
import {
  transitionModelProvider,
  type ApiProviderSnapshot,
} from "./modelProviderState.js";

type LocalRowId = string;

/**
 * Component-local ownership for provider-transition snapshots.
 *
 * Row ids are opaque counters and never enter ModelEntry or persisted config.
 * Local edits explicitly preserve/reorder these ids. Any externally supplied
 * array resets the store, so refresh, cancel, and parent-owned removal cannot
 * make a surviving row inherit another row's credential snapshot.
 */
export class ModelProviderStateStorage {
  private nextRowId = 0;
  private rowIds: LocalRowId[] = [];
  private snapshots = new Map<LocalRowId, ApiProviderSnapshot>();
  private currentRows: readonly ModelEntry[];
  private pendingInternalRows: readonly ModelEntry[] | null = null;

  constructor(rows: readonly ModelEntry[]) {
    this.currentRows = rows;
    this.rowIds = rows.map(() => this.createRowId());
  }

  /** Accept parent props, clearing private state unless they acknowledge our exact emission. */
  synchronize(rows: readonly ModelEntry[]): void {
    if (rows === this.pendingInternalRows) {
      this.currentRows = rows;
      this.pendingInternalRows = null;
      return;
    }
    if (rows === this.currentRows) return;
    this.reset(rows);
  }

  /** Explicit external reset used by tests and future parent-owned reset hooks. */
  reset(rows: readonly ModelEntry[]): void {
    this.snapshots.clear();
    this.rowIds = rows.map(() => this.createRowId());
    this.currentRows = rows;
    this.pendingInternalRows = null;
  }

  transitionProvider(
    rows: readonly ModelEntry[],
    index: number,
    targetProvider: string,
  ): ModelEntry[] {
    this.synchronize(rows);
    const rowId = this.requireRowId(index);
    const transition = transitionModelProvider(
      rows[index],
      targetProvider,
      this.snapshots.get(rowId) ?? null,
    );
    if (transition.snapshot) {
      this.snapshots.set(rowId, transition.snapshot);
    } else {
      this.snapshots.delete(rowId);
    }
    const nextRows = [...rows];
    nextRows[index] = transition.model;
    this.acceptInternal(nextRows, [...this.rowIds]);
    return nextRows;
  }

  /** Preserve row identity while replacing one row; optionally discard its snapshot. */
  replaceRow(
    rows: readonly ModelEntry[],
    index: number,
    model: ModelEntry,
    options: { clearSnapshot?: boolean } = {},
  ): ModelEntry[] {
    this.synchronize(rows);
    const rowId = this.requireRowId(index);
    if (options.clearSnapshot) this.snapshots.delete(rowId);
    const nextRows = [...rows];
    nextRows[index] = model;
    this.acceptInternal(nextRows, [...this.rowIds]);
    return nextRows;
  }

  preserveSameOrder(
    rows: readonly ModelEntry[],
    nextRows: readonly ModelEntry[],
  ): void {
    this.synchronize(rows);
    if (nextRows.length !== this.rowIds.length) {
      this.reset(nextRows);
      return;
    }
    this.acceptInternal(nextRows, [...this.rowIds]);
  }

  appendRow(rows: readonly ModelEntry[], nextRows: readonly ModelEntry[]): void {
    this.synchronize(rows);
    if (nextRows.length !== this.rowIds.length + 1) {
      this.reset(nextRows);
      return;
    }
    this.acceptInternal(nextRows, [...this.rowIds, this.createRowId()]);
  }

  moveRow(
    rows: readonly ModelEntry[],
    nextRows: readonly ModelEntry[],
    fromIndex: number,
    toIndex: number,
  ): void {
    this.synchronize(rows);
    if (
      nextRows.length !== this.rowIds.length
      || fromIndex < 0
      || fromIndex >= this.rowIds.length
      || toIndex < 0
      || toIndex >= this.rowIds.length
    ) {
      this.reset(nextRows);
      return;
    }
    const nextIds = [...this.rowIds];
    const [moved] = nextIds.splice(fromIndex, 1);
    nextIds.splice(toIndex, 0, moved);
    this.acceptInternal(nextRows, nextIds);
  }

  private createRowId(): LocalRowId {
    this.nextRowId += 1;
    return `model-row-${this.nextRowId}`;
  }

  private requireRowId(index: number): LocalRowId {
    const rowId = this.rowIds[index];
    if (!rowId || index < 0 || index >= this.currentRows.length) {
      throw new RangeError(`Model row index ${index} is out of range.`);
    }
    return rowId;
  }

  private acceptInternal(
    rows: readonly ModelEntry[],
    rowIds: LocalRowId[],
  ): void {
    this.currentRows = rows;
    this.pendingInternalRows = rows;
    this.rowIds = rowIds;
  }
}
