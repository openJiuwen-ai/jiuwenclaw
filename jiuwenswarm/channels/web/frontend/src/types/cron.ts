/** 后端 cron.job.* 系列 RPC 实际收发的字段，对齐 jiuwenswarm/gateway/cron/models.py 的 CronJob.to_dict() */
export interface CronJobDTO {
  id: string;
  name: string;
  enabled: boolean;
  expired: boolean;
  cron_expr: string;
  timezone: string;
  wake_offset_seconds: number;
  description: string;
  targets: string;
  created_at: number | null;
  updated_at: number | null;
  session_id?: string;
  chat_type?: string;
  mode?: string;
  delete_after_run?: boolean;
  timeout_seconds?: number;
  project_id: string;
  last_session_id?: string;
  model_name?: string;
}

/** create/update 请求体；project_dir 而非 project_id，见 CronController.create_job */
export interface CronJobUpsertParams {
  name: string;
  cron_expr: string;
  timezone: string;
  enabled: boolean;
  description: string;
  targets: string;
  wake_offset_seconds?: number;
  model_name?: string;
  project_dir?: string;
  mode?: string;
}

/** UI 层展示用结构，来自 CronJobDTO 派生（见 cronJobToUI） */
export interface CronTaskUI {
  id: string;
  name: string;
  projectId: string;
  projectName: string | null;
  description: string;
  modelName: string | null;
  cronExpr: string;
  timezone: string;
  enabled: boolean;
  expired: boolean;
  deliveryChannel: string;
}

export interface CronTemplateUI {
  id: string;
  icon: 'trend' | 'newspaper' | 'briefcase';
  titleKey: string;
  descriptionKey: string;
  cronExpr: string;
}
