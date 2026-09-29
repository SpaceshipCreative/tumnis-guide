// The project page's data as the generated schemas parse it (P0-24).
import type * as z from "zod";

import type { zBoardOut, zProjectOut, zTaskOut } from "../../api/zod.gen";

export type Project = z.output<typeof zProjectOut>;
export type Task = z.output<typeof zTaskOut>;
export type Board = z.output<typeof zBoardOut>;
