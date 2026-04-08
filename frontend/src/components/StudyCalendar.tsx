import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin, { EventResizeDoneArg } from "@fullcalendar/interaction";
import timeGridPlugin from "@fullcalendar/timegrid";
import { EventDropArg } from "@fullcalendar/core";
import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "../lib/api";

type StudyBlockType = "lecture" | "reading" | "assignment" | "review" | "exam_prep";

type StudyBlock = {
  id: string;
  title: string;
  start_at: string;
  end_at: string;
  block_type: StudyBlockType;
  course_color?: string;
};

type ScheduleResponse = {
  blocks: StudyBlock[];
};

const blockColors: Record<StudyBlockType, string> = {
  lecture: "#0284C7",
  reading: "#0891B2",
  assignment: "#D97706",
  review: "#16A34A",
  exam_prep: "#7C3AED",
};

function toEvent(block: StudyBlock) {
  const color = block.course_color || blockColors[block.block_type];

  return {
    id: block.id,
    title: block.title,
    start: block.start_at,
    end: block.end_at,
    backgroundColor: color,
    borderColor: color,
    extendedProps: {
      blockType: block.block_type,
    },
  };
}

export function StudyCalendar() {
  const queryClient = useQueryClient();

  const scheduleQuery = useQuery({
    queryKey: ["schedule"],
    queryFn: () => apiFetch<ScheduleResponse>("/schedule"),
  });

  const patchMutation = useMutation({
    mutationFn: async ({ id, start, end }: { id: string; start: string; end: string }) =>
      apiFetch<StudyBlock>(`/schedule/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          start_at: start,
          end_at: end,
        }),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["schedule"] });
    },
  });

  const events = useMemo(() => {
    if (!scheduleQuery.data) {
      return [];
    }
    return scheduleQuery.data.blocks.map(toEvent);
  }, [scheduleQuery.data]);

  async function syncDroppedEvent({
    event,
    revert,
  }: {
    event: EventDropArg["event"] | EventResizeDoneArg["event"];
    revert: () => void;
  }) {
    if (!event.start || !event.end) {
      revert();
      return;
    }

    try {
      await patchMutation.mutateAsync({
        id: event.id,
        start: event.start.toISOString(),
        end: event.end.toISOString(),
      });
    } catch {
      revert();
    }
  }

  if (scheduleQuery.isLoading) {
    return <p>Loading schedule...</p>;
  }

  if (scheduleQuery.error) {
    return <p>Unable to load schedule.</p>;
  }

  return (
    <section aria-label="Study block calendar">
      <FullCalendar
        plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
        initialView="timeGridWeek"
        headerToolbar={{
          left: "prev,next today",
          center: "title",
          right: "dayGridMonth,timeGridWeek,timeGridDay",
        }}
        events={events}
        editable
        eventDrop={(arg: EventDropArg) =>
          syncDroppedEvent({
            event: arg.event,
            revert: arg.revert,
          })
        }
        eventResize={(arg: EventResizeDoneArg) =>
          syncDroppedEvent({
            event: arg.event,
            revert: arg.revert,
          })
        }
        nowIndicator
        height="auto"
      />
      {patchMutation.isPending && <p>Saving schedule changes...</p>}
    </section>
  );
}
