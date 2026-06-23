/**
 * Tool 抽象类 — 所有工具的基类
 *
 * 定义工具的标准接口：name, description, parameters (JSON Schema), execute()
 * Phase 5 中具体的工具将实现此接口。
 *
 * 与 Python 版 tools/base.py 保持 1:1 语义映射。
 */

/** 工具执行结果 */
export interface ToolResult {
  /** 执行是否成功 */
  success: boolean;
  /** 返回数据（成功时） */
  data: string;
  /** 错误信息（失败时） */
  error: string;
}

/** 工具抽象基类 */
export abstract class Tool {
  /** 工具名称 */
  abstract readonly name: string;

  /** 工具描述（给 LLM 看的） */
  abstract readonly description: string;

  /** 工具参数 JSON Schema */
  abstract readonly parameters: Record<string, unknown>;

  /**
   * 执行工具
   *
   * @param kwargs - 工具参数
   * @returns 执行结果
   */
  abstract execute(kwargs: Record<string, unknown>): Promise<ToolResult>;
}
