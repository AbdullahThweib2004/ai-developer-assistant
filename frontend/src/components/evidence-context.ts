import { createContext, useContext } from "react";
import type { Source } from "../lib/types";

export interface EvidenceContextValue {
  sources: Record<string, Source>;
  invalid: Set<string>;
  open: (citeId: string) => void;
}

export const EvidenceContext = createContext<EvidenceContextValue>({ sources: {}, invalid: new Set(), open: () => {} });

export function useEvidence(): EvidenceContextValue {
  return useContext(EvidenceContext);
}
