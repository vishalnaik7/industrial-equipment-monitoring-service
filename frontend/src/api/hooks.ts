// React Query hooks: one place for cache keys, polling intervals and invalidation after writes.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { MachineFilters, VendorSlug } from "./types";

export const keys = {
  meta: ["meta"] as const,
  ready: ["ready"] as const,
  reference: ["reference"] as const,
  summary: (plant?: string) => ["summary", plant ?? "all"] as const,
  machines: (f: MachineFilters) => ["machines", f] as const,
  machine: (id: string) => ["machine", id] as const,
  batches: (vendor?: string) => ["batches", vendor ?? "all"] as const,
  batch: (id: string) => ["batch", id] as const,
  deadLetters: (vendor?: string, code?: string) => ["dead-letters", vendor ?? "", code ?? ""] as const,
  trace: (vendor: string, id: string) => ["trace", vendor, id] as const,
  samples: ["samples"] as const,
};

export const useMeta = () => useQuery({ queryKey: keys.meta, queryFn: api.meta, staleTime: 60_000 });
export const useReference = () => useQuery({ queryKey: keys.reference, queryFn: api.reference, staleTime: 300_000 });
export const useReady = () => useQuery({ queryKey: keys.ready, queryFn: api.ready, refetchInterval: 5_000, retry: false });

export const useSummary = (plant?: string, autoRefresh = false) =>
  useQuery({ queryKey: keys.summary(plant), queryFn: () => api.summary(plant), refetchInterval: autoRefresh ? 10_000 : false });

export const useMachines = (f: MachineFilters) =>
  useQuery({ queryKey: keys.machines(f), queryFn: () => api.machines(f), placeholderData: keepPreviousData });

export const useMachine = (id: string | undefined) =>
  useQuery({ queryKey: keys.machine(id ?? ""), queryFn: () => api.machine(id!), enabled: !!id });

export const useBatches = (vendor?: string) =>
  useQuery({ queryKey: keys.batches(vendor), queryFn: () => api.batches({ limit: 200, vendor }) });

/** Polls once a second while the worker is still processing the batch. */
export const useBatch = (id: string | undefined) =>
  useQuery({
    queryKey: keys.batch(id ?? ""), queryFn: () => api.batch(id!), enabled: !!id,
    refetchInterval: (q) => (q.state.data?.state === "processing" ? 1_000 : false),
  });

export const useDeadLetters = (vendor?: string, code?: string) =>
  useQuery({ queryKey: keys.deadLetters(vendor, code), queryFn: () => api.deadLetters({ limit: 500, vendor, error_code: code }) });

export const useTrace = (vendor: VendorSlug | undefined, id: string | undefined) =>
  useQuery({ queryKey: keys.trace(vendor ?? "", id ?? ""), queryFn: () => api.trace(vendor!, id!), enabled: !!vendor && !!id });

export const useSamples = () => useQuery({ queryKey: keys.samples, queryFn: api.samples, staleTime: Infinity });

/** After data changes server-side, everything derived from it is stale. */
export function useInvalidateDerived() {
  const qc = useQueryClient();
  return () => Promise.all(["summary", "machines", "machine", "batches", "dead-letters", "ready", "trace"].map((k) =>
    qc.invalidateQueries({ queryKey: [k] })));
}

export function useIngest() {
  const invalidate = useInvalidateDerived();
  return useMutation({
    mutationFn: ({ vendor, body }: { vendor: VendorSlug; body: string | object }) => api.ingest(vendor, body),
    onSuccess: () => invalidate(),
  });
}

export const useValidate = () =>
  useMutation({ mutationFn: ({ vendor, body }: { vendor: VendorSlug; body: string }) => api.validate(vendor, body) });

export function useReplay() {
  const invalidate = useInvalidateDerived();
  return useMutation({ mutationFn: (rawId: number) => api.replay(rawId), onSuccess: () => invalidate() });
}
