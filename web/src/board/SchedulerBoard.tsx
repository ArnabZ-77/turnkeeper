/**
 * The Bryntum Scheduler (trial) rotation board, through the official React wrapper.
 *
 * Loaded with a dynamic import from RotationBoard so a runtime failure of the trial bundle falls
 * back to the CSS grid instead of blanking the page. Config objects live outside render; data
 * (resources, events, time ranges, axis) comes from board/mapping.ts and is synced by the wrapper.
 */

import { useCallback, useEffect, useRef } from "react";
import { BryntumScheduler } from "@bryntum/scheduler-react";
import { StringHelper } from "@bryntum/scheduler";
import type { DomClassList, DomConfig, EventModel, Model } from "@bryntum/scheduler";
import type { BoardMapping, EventMeta } from "./mapping";
import { contributionCls, potCls, tooltipHtml } from "./mapping";
import "../theme/bryntum.css";

export interface SchedulerBoardProps {
  mapping: BoardMapping;
  onSelect: (meta: EventMeta | null) => void;
}

function metaOf(record: Model | EventModel | undefined | null): EventMeta | null {
  if (!record) return null;
  const tk = record.get("tk") as EventMeta | undefined;
  return tk && (tk.kind === "contribution" || tk.kind === "pot") ? tk : null;
}

function addCls(target: DomClassList | string, cls: string): DomClassList | string {
  if (typeof target === "string") return `${target} ${cls}`.trim();
  target.add(cls);
  return target;
}

type RendererContext = Parameters<NonNullable<React.ComponentProps<typeof BryntumScheduler>["eventRenderer"]>>[0];

/** Status classes go on the wrapper (theme/bryntum.css maps them to --b-primary); the label is escaped text. */
function eventRenderer({ eventRecord, renderData }: RendererContext): string {
  const meta = metaOf(eventRecord);
  if (meta) {
    renderData.wrapperCls = addCls(renderData.wrapperCls, meta.kind === "pot" ? potCls(meta) : contributionCls(meta));
  }
  const text = StringHelper.encodeHtml(String(eventRecord.name ?? ""));
  // T-3 forecast flag: a small fixed-width badge (static markup, no member text).
  return meta?.kind === "contribution" && meta.predicted ? `${text}<span class="tk-predicted-badge">predicted</span>` : text;
}

const COLUMNS = [
  {
    text: "Member",
    field: "name",
    width: 190,
    htmlEncode: false,
    renderer: ({ record }: { record: Model }): DomConfig => {
      const name = String(record.get("name") ?? "");
      const handle = String(record.get("handle") ?? "");
      const pos = record.get("rotationPos");
      const wallet = String(record.get("wallet") ?? "PAYPAL");
      const vaulted = Boolean(record.get("vaulted"));
      const recipientRound = record.get("recipientRound") as number | null;
      const label = record.get("label") as string | null;
      const moved = Boolean(record.get("moved"));
      const meta: DomConfig[] = [
        { tag: "span", text: `@${handle} · #${pos ?? "?"} · ${wallet}${vaulted ? "" : " · not vaulted"}` },
      ];
      if (recipientRound) {
        meta.push({ tag: "span", className: "tk-member-recipient", text: ` · pot in round ${recipientRound}` });
      }
      const children: DomConfig[] = [
        { tag: "span", className: "tk-member-name", text: name },
        { tag: "span", className: "tk-member-meta", children: meta },
      ];
      if (label) children.push({ tag: "span", className: "tk-member-label", text: label });
      return { className: moved ? "tk-member tk-member-moved" : "tk-member", children };
    },
  },
];

const TIME_RANGES_FEATURE = {
  showHeaderElements: true,
  enableResizing: false,
  showCurrentTimeLine: false,
};

const EVENT_TOOLTIP_FEATURE = {
  hoverDelay: 150,
  hideDelay: 200,
  allowOver: true,
  template: ({ eventRecord }: { eventRecord: EventModel }): string => {
    const meta = metaOf(eventRecord);
    return meta ? tooltipHtml(meta) : StringHelper.encodeHtml(String(eventRecord.name ?? ""));
  },
};

/** Read-only board: no editing, dragging, resizing, creating or context menus. */
const STATIC_PROPS = {
  cls: "tk-scheduler",
  readOnly: true,
  rowHeight: 64,
  barMargin: 6,
  eventStyle: "filled" as const,
  milestoneLayoutMode: "estimate" as const,
  zoomOnMouseWheel: false,
  zoomOnTimeAxisDoubleClick: false,
  createEventOnDblClick: false,
  multiEventSelect: false,
  enableEventAnimations: true,
  useInitialAnimation: "fade-in",
  weekStartDay: 1,
  columnLinesFeature: true,
  stripeFeature: true,
  timeRangesFeature: TIME_RANGES_FEATURE,
  eventTooltipFeature: EVENT_TOOLTIP_FEATURE,
  cellEditFeature: false,
  cellMenuFeature: false,
  headerMenuFeature: false,
  eventEditFeature: false,
  eventDragFeature: false,
  eventDragCreateFeature: false,
  eventResizeFeature: false,
  eventMenuFeature: false,
  scheduleMenuFeature: false,
  scheduleTooltipFeature: false,
  timeAxisHeaderMenuFeature: false,
  regionResizeFeature: false,
  sortFeature: false,
  groupFeature: false,
  columnReorderFeature: false,
  eventCopyPasteFeature: false,
  rowCopyPasteFeature: false,
  eventRenderer,
};

export default function SchedulerBoard({ mapping, onSelect }: SchedulerBoardProps) {
  const ref = useRef<BryntumScheduler>(null);

  const onEventClick = useCallback(
    ({ eventRecord }: { eventRecord: EventModel }) => onSelect(metaOf(eventRecord)),
    [onSelect],
  );

  // Keep the first open round in view after the data loads.
  useEffect(() => {
    const instance = ref.current?.instance;
    if (!instance) return;
    const pot = mapping.events.find((e) => e.tk.kind === "pot" && e.tk.status !== "PAID" && e.tk.status !== "VOID");
    const target = pot?.startDate ?? mapping.startDate;
    try {
      void instance.scrollToDate(target, { block: "center", animate: false });
    } catch {
      /* the time axis may not be laid out yet; harmless */
    }
  }, [mapping.startDate, mapping.events]);

  return (
    <BryntumScheduler
      ref={ref}
      {...STATIC_PROPS}
      columns={COLUMNS}
      resources={mapping.resources}
      events={mapping.events}
      timeRanges={mapping.timeRanges}
      startDate={mapping.startDate}
      endDate={mapping.endDate}
      viewPreset={mapping.viewPreset}
      tickSize={mapping.tickSize}
      onEventClick={onEventClick}
    />
  );
}
