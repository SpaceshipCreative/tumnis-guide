// The project page's data as the generated schemas parse it (P0-24). A project is either
// the generated type (what Query holds) or the zod output (what the factories make); the
// two differ only in how optional fields are spelled.
import type * as z from "zod";

import type { ProjectOut } from "../../api/types.gen";
import type {
  zBoardOut,
  zDocumentDto,
  zProjectOut,
  zTaskOut,
} from "../../api/zod.gen";

export type Project = ProjectOut | z.output<typeof zProjectOut>;
export type Task = z.output<typeof zTaskOut>;
export type Board = z.output<typeof zBoardOut>;
export type Brief = z.output<typeof zDocumentDto>;
