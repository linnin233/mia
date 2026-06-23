/**
 * WeatherTool — wttr.in 天气查询
 *
 * 免费天气查询服务，无需 API Key。
 * 返回温度、天气状况、湿度、风速等。
 *
 * 与 Python 版 tools/weather.py 保持 1:1 语义映射。
 */

import { Tool, type ToolResult } from './base.js';

/** wttr.in 天气代码 → 中文描述映射 */
const WEATHER_CODES: Record<string, string> = {
  '113': '晴天', '116': '晴间多云',
  '119': '多云', '122': '阴天',
  '143': '雾', '176': '阵雨',
  '179': '小雪', '182': '雨夹雪',
  '185': '冻雨', '200': '雷阵雨',
  '227': '暴风雪', '230': '暴风雪',
  '248': '雾', '260': '大雾',
  '263': '小雨', '266': '小雨',
  '281': '冻雨', '284': '冻雨',
  '293': '小雨', '296': '小雨',
  '299': '中雨', '302': '中雨',
  '305': '大雨', '308': '暴雨',
  '311': '小雨夹雪', '314': '中雨夹雪',
  '317': '大雨夹雪', '320': '雨夹雪',
  '323': '小雪', '326': '小雪',
  '329': '中雪', '332': '中雪',
  '335': '大雪', '338': '大雪',
  '350': '冰雹', '353': '阵雨',
  '356': '中雨', '359': '暴雨',
  '362': '小雪', '365': '中雪',
  '368': '大雪', '371': '大雪',
  '374': '冰雹', '377': '冰雹',
  '386': '雷阵雨', '389': '雷暴',
  '392': '雷暴雪', '395': '大雪',
};

/** wttr.in 小时数据项 */
interface HourlyData {
  time?: string;
  tempC?: string;
  humidity?: string;
  weatherCode?: string;
  windspeedKmph?: string;
  winddir16Point?: string;
  chanceofrain?: string;
}

/** wttr.in 日数据 */
interface DayData {
  date?: string;
  mintempC?: string;
  maxtempC?: string;
  astronomy?: Array<{ sunrise?: string; sunset?: string }>;
  hourly?: HourlyData[];
}

/** wttr.in JSON 响应 */
interface WttrResponse {
  weather?: DayData[];
}

/**
 * WeatherTool — wttr.in 天气查询工具
 *
 * 适用于: 天气查询、出行建议。
 */
export class WeatherTool extends Tool {
  readonly name = 'weather';
  readonly description =
    '查询指定城市的天气信息，支持今天和未来几天的天气预报。' +
    '返回: 温度（最高/最低）、天气状况、湿度、风速、风向等。' +
    '适用于: 天气查询、出行建议。';
  readonly parameters = {
    type: 'object',
    properties: {
      city: {
        type: 'string',
        description: "城市名称（中文或英文），如 '嘉兴'、'Beijing'、'上海'",
      },
      days: {
        type: 'integer',
        description: '查询天数 (1-3, 默认2)',
      },
    },
    required: ['city'],
  };

  /** 将 wttr.in 天气代码转为中文描述 */
  private _translateWeatherCode(code: string): string {
    return WEATHER_CODES[code] || `未知(${code})`;
  }

  /** 格式化 wttr.in JSON 为可读文本 */
  private _formatWeather(data: WttrResponse, city: string, days: number): string {
    const weatherList = (data.weather || []).slice(0, days);
    if (weatherList.length === 0) {
      return `未获取到 ${city} 的天气数据。`;
    }

    const lines: string[] = [`📍 ${city} 天气预报`, '='.repeat(40)];

    for (const dayData of weatherList) {
      const date = dayData.date || '未知日期';
      const mintemp = dayData.mintempC || '?';
      const maxtemp = dayData.maxtempC || '?';
      const astro = (dayData.astronomy || [{}])[0] || {};
      const sunrise = astro.sunrise || '?';
      const sunset = astro.sunset || '?';

      lines.push(`\n📅 ${date}`);
      lines.push(`   温度: ${mintemp}°C ~ ${maxtemp}°C`);
      lines.push(`   日出: ${sunrise} | 日落: ${sunset}`);

      // 从 hourly 数据提取白天信息
      const hourly = (dayData.hourly || []).filter((h) => {
        const hour = parseInt(String(h.time || '0').padStart(3, '0').slice(0, 2));
        return !isNaN(hour) && hour >= 6 && hour <= 20;
      });

      const targetHours = hourly.length > 0 ? hourly : dayData.hourly || [];

      if (targetHours.length > 0) {
        // 最常见天气
        const codeCounts = new Map<string, number>();
        for (const h of targetHours) {
          if (h.weatherCode) {
            codeCounts.set(h.weatherCode, (codeCounts.get(h.weatherCode) || 0) + 1);
          }
        }
        if (codeCounts.size > 0) {
          const mostCommon = [...codeCounts.entries()].sort((a, b) => b[1] - a[1])[0]![0]!;
          lines.push(`   天气: ${this._translateWeatherCode(mostCommon)}`);
        }

        // 平均湿度
        const humidities = targetHours
          .map((h) => parseInt(h.humidity || '0'))
          .filter((n) => !isNaN(n) && n > 0);
        if (humidities.length > 0) {
          lines.push(`   湿度: ~${Math.round(humidities.reduce((a, b) => a + b) / humidities.length)}%`);
        }

        // 风速
        const windSpeeds = targetHours
          .map((h) => parseInt(h.windspeedKmph || '0'))
          .filter((n) => !isNaN(n) && n > 0);
        if (windSpeeds.length > 0) {
          const avgWind = Math.round(windSpeeds.reduce((a, b) => a + b) / windSpeeds.length);
          const dirCounts = new Map<string, number>();
          for (const h of targetHours) {
            if (h.winddir16Point) {
              dirCounts.set(h.winddir16Point, (dirCounts.get(h.winddir16Point) || 0) + 1);
            }
          }
          const mostDir = [...dirCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || '?';
          lines.push(`   风速: ~${avgWind} km/h (${mostDir})`);
        }

        // 降雨概率
        const rainChances = targetHours
          .map((h) => parseInt(h.chanceofrain || '0'))
          .filter((n) => !isNaN(n));
        if (rainChances.length > 0) {
          lines.push(`   降雨概率: 最高 ${Math.max(...rainChances)}%`);
        }
      }
    }

    lines.push('\n数据来源: wttr.in');
    return lines.join('\n');
  }

  /**
   * 查询天气
   *
   * @param kwargs.city - 城市名称
   * @param kwargs.days - 查询天数 (1-3)
   */
  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    const city = String(kwargs['city'] || '');
    const days = Math.max(1, Math.min(Number(kwargs['days']) || 2, 3));

    if (!city) {
      return { success: false, data: '', error: '城市名称不能为空' };
    }

    try {
      const url = `https://wttr.in/${encodeURIComponent(city)}?format=j1&lang=zh`;
      const response = await fetch(url, {
        signal: AbortSignal.timeout(15_000),
      });

      if (!response.ok) {
        return { success: false, data: '', error: `天气查询 HTTP 错误 (${response.status})` };
      }

      const data = (await response.json()) as WttrResponse;

      if (!data || !data.weather) {
        return {
          success: false,
          data: '',
          error: `未找到城市 '${city}' 的天气数据，请检查城市名称是否正确。`,
        };
      }

      const formatted = this._formatWeather(data, city, days);
      return { success: true, data: formatted, error: '' };
    } catch (err) {
      if (err instanceof DOMException && err.name === 'TimeoutError') {
        return { success: false, data: '', error: '天气查询超时，请稍后重试。' };
      }
      return {
        success: false,
        data: '',
        error: `天气查询失败: ${err instanceof Error ? err.message : String(err)}`,
      };
    }
  }
}
