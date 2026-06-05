// 券商 API 同步相关接口

const BASE = "/api/broker-sync";

export interface SyncStatusItem {
  broker: string;
  platform: string;
  last_sync_time: string | null;
  last_sync_status: string | null;  // "success" | "failed" | "running" | "never"
  last_position_count: number | null;
  error_message: string | null;
}

export interface SyncStatusResponse {
  brokers: SyncStatusItem[];
}

export interface TriggerResponse {
  message: string;
  brokers_triggered: string[];
}

export async function getSyncStatus(): Promise<SyncStatusResponse> {
  const res = await fetch(`${BASE}/status`);
  if (!res.ok) throw new Error(`获取同步状态失败: ${res.status}`);
  return res.json();
}

export async function triggerSync(broker: "tiger" | "futu" | "snowball" | "guojin" | "all" = "all"): Promise<TriggerResponse> {
  const res = await fetch(`${BASE}/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ broker, triggered_by: "manual" }),
  });
  if (!res.ok) throw new Error(`触发同步失败: ${res.status}`);
  return res.json();
}
