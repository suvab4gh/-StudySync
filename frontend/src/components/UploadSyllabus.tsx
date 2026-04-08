import { ChangeEvent, DragEvent, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { apiFormDataFetch } from "../lib/api";

type UploadResponse = {
  courseId: string;
  parsedItems: number;
};

type UploadSyllabusProps = {
  maxBytes?: number;
  onUploaded?: (result: UploadResponse) => void;
};

const ACCEPTED_TYPES = ["application/pdf", "text/plain", "text/markdown"];

export function UploadSyllabus({ maxBytes = 8 * 1024 * 1024, onUploaded }: UploadSyllabusProps) {
  const queryClient = useQueryClient();
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fileSchema = useMemo(
    () =>
      z
        .custom<File>((value: unknown) => value instanceof File, "A file is required")
        .refine((file: File) => file.size <= maxBytes, `File must be <= ${Math.round(maxBytes / 1024 / 1024)}MB`)
        .refine((file: File) => ACCEPTED_TYPES.includes(file.type), "Only PDF, TXT, and Markdown are allowed"),
    [maxBytes],
  );

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const parsed = fileSchema.safeParse(file);
      if (!parsed.success) {
        throw new Error(parsed.error.issues[0]?.message || "Invalid file");
      }

      const formData = new FormData();
      formData.append("file", file);

      return apiFormDataFetch<UploadResponse>("/upload", formData);
    },
    onSuccess: async (data: UploadResponse) => {
      setError(null);
      onUploaded?.(data);
      await queryClient.invalidateQueries({ queryKey: ["courses"] });
      await queryClient.invalidateQueries({ queryKey: ["schedule"] });
    },
    onError: (mutationError: unknown) => {
      const message = mutationError instanceof Error ? mutationError.message : "Upload failed";
      setError(message);
    },
  });

  function handleFileSelect(file: File | null) {
    if (!file) {
      return;
    }
    uploadMutation.mutate(file);
  }

  return (
    <section aria-label="Syllabus uploader" style={{ maxWidth: 540 }}>
      <label
        onDragOver={(event: DragEvent<HTMLLabelElement>) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(event: DragEvent<HTMLLabelElement>) => {
          event.preventDefault();
          setDragActive(false);
          handleFileSelect(event.dataTransfer.files?.[0] ?? null);
        }}
        style={{
          border: dragActive ? "2px solid #0F766E" : "2px dashed #94A3B8",
          borderRadius: 12,
          display: "block",
          padding: 20,
          backgroundColor: dragActive ? "#CCFBF1" : "#F8FAFC",
          transition: "all 160ms ease",
          cursor: "pointer",
        }}
      >
        <input
          type="file"
          accept=".pdf,.txt,.md"
          hidden
          onChange={(event: ChangeEvent<HTMLInputElement>) => handleFileSelect(event.target.files?.[0] ?? null)}
        />
        <strong>Drop syllabus here</strong>
        <p style={{ marginTop: 8, marginBottom: 0 }}>or click to upload PDF, TXT, or Markdown.</p>
      </label>

      {uploadMutation.isPending && <p style={{ color: "#0F172A" }}>Uploading and parsing syllabus...</p>}
      {error && <p style={{ color: "#B91C1C" }}>{error}</p>}
      {uploadMutation.data && (
        <p style={{ color: "#065F46" }}>
          Parsed {uploadMutation.data.parsedItems} syllabus items for course {uploadMutation.data.courseId}.
        </p>
      )}
    </section>
  );
}
