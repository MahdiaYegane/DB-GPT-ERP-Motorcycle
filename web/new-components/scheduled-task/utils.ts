/**
 * 定时任务相关的共享工具函数
 */

/** 将 cron 表达式转为友好中文描述 */
export function cronToLabel(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dayOfMonth, , dayOfWeek] = parts;
  const weekMap: Record<string, string> = {
    '0': 'یکشنبه',
    '1': 'دوشنبه',
    '2': 'سه‌شنبه',
    '3': 'چهارشنبه',
    '4': 'پنجشنبه',
    '5': 'جمعه',
    '6': 'شنبه',
    '7': 'یکشنبه',
  };
  if (hour === '*' && dayOfMonth === '*' && dayOfWeek === '*') {
    return `هر ساعت، دقیقه ${minute}`;
  }
  if (dayOfMonth === '*' && dayOfWeek === '*') {
    return `هر روز ${hour}:${String(minute).padStart(2, '0')}`;
  }
  if (dayOfMonth === '*' && dayOfWeek !== '*') {
    return `هر ${weekMap[dayOfWeek] ?? `روز ${dayOfWeek}`} ${hour}:${String(minute).padStart(2, '0')}`;
  }
  if (dayOfWeek === '*' && dayOfMonth !== '*') {
    return `هر ماه، روز ${dayOfMonth} ساعت ${hour}:${String(minute).padStart(2, '0')}`;
  }
  return cron;
}
