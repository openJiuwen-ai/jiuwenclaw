export type ProjectInfo = {
  project_id: string;
  name: string;
  project_dir: string;
  pinned: boolean;
  pin_order: number;
  is_default: boolean;
  hidden: boolean;
  session_count: number;
  last_message_at: number | null;
  last_user_message_at: number | null;
  created_at: number;
};
