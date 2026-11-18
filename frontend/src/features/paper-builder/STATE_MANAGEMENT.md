# Paper Builder state boundary

The editable `PaperDraft` lives in the feature's Zustand store. It is an editor buffer: fast local
keystrokes, stable section/slot addresses, dirty paths, lock placeholders, and (in P5-T5) undo/redo
history and autosave state belong here.

TanStack Query owns remote resources and server snapshots: curriculum lists, feasibility responses,
persisted paper fetches, and mutation status. A paper fetch hydrates/rebases the Zustand buffer; it
does not remain the object that form controls mutate. Successful saves replace the Query snapshot and
mark the acknowledged paths clean in the buffer.

P5-T6 should apply ordered SSE events to both layers deliberately: patch the Query snapshot as the
server truth, then materialize the same `slotId`/`sectionId` patch into the buffer unless that subtree
has a conflicting dirty path. `generation.lastSequence` is the replay/deduplication checkpoint. This
keeps Query's cache serializable and server-shaped while preserving editor semantics in one place.

P5-T5 keeps its bounded user-edit history and edit/save sequence in Zustand. Server-driven generation
patches update and autosave the buffer but never enter undo history. TanStack Query owns the remote
`paper_editor_drafts` resource and mutation lifecycle; successful CAS saves replace that query snapshot
and acknowledge the matching Zustand edit sequence. The 1,000 ms autosave timer is independent of the
650 ms feasibility timer. A 409 pauses autosave without rebasing the local buffer until the teacher
chooses the server copy or saves the local copy under a new draft ID.
