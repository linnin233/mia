/**
 * MessageBus 单元测试
 *
 * 对应 Python 版 test_full_pipeline.py 中的 Test 2 (Message Bus Mirror)
 */

import { describe, it, expect } from 'vitest';
import { MessageBus } from '../src/bus/bus.js';
import { MessageType, makeSendText, makeConversationDone } from '../src/bus/message.js';

describe('MessageBus', () => {
  it('应该成功订阅和接收消息', async () => {
    const bus = new MessageBus(10);
    await bus.start();
    await bus.subscribe('test_agent');

    const msg = makeConversationDone('test_session');
    await bus.publish({ ...msg, target: 'test_agent' });

    const received = await bus.receive('test_agent', 1000);
    expect(received).not.toBeNull();
    expect(received!.msg_type).toBe(MessageType.CONVERSATION_DONE);
    expect(received!.session_id).toBe('test_session');

    await bus.stop();
  });

  it('超时应该返回 null', async () => {
    const bus = new MessageBus(10);
    await bus.start();
    await bus.subscribe('empty_agent');

    const received = await bus.receive('empty_agent', 100);
    expect(received).toBeNull();

    await bus.stop();
  });

  it('广播消息应投递到除 source 外的所有订阅者', async () => {
    const bus = new MessageBus(10);
    await bus.start();
    await bus.subscribe('a');
    await bus.subscribe('b');

    const msg = makeSendText('hello', 's1');
    msg.source = 'a';
    msg.target = 'broadcast';
    await bus.publish(msg);

    // a 不应该收到（source 是 a）
    const aMsg = await bus.receive('a', 500);
    expect(aMsg).toBeNull();

    // b 应该收到
    const bMsg = await bus.receive('b', 500);
    expect(bMsg).not.toBeNull();
    expect(bMsg!.msg_type).toBe(MessageType.SEND_TEXT);

    await bus.stop();
  });

  it('镜像订阅应该额外投递指定类型的消息', async () => {
    const bus = new MessageBus(10);
    await bus.start();
    await bus.subscribe('sender');
    await bus.subscribe('memory');

    bus.subscribeMirror(MessageType.SEND_TEXT, 'memory');

    const msg = makeSendText('hi', 's2');
    msg.target = 'sender';
    await bus.publish(msg);

    // sender 应该收到
    const senderMsg = await bus.receive('sender', 500);
    expect(senderMsg).not.toBeNull();

    // memory 也应该收到（镜像）
    const memMsg = await bus.receive('memory', 500);
    expect(memMsg).not.toBeNull();
    expect(memMsg!.msg_type).toBe(MessageType.SEND_TEXT);

    await bus.stop();
  });

  it('队列满时应丢弃旧消息并放入新消息', async () => {
    const bus = new MessageBus(2); // 小队列
    await bus.start();
    await bus.subscribe('small');

    // 放入 3 条消息，队列满 2 条
    const msg1 = makeSendText('msg1', 's');
    const msg2 = makeSendText('msg2', 's');
    const msg3 = makeSendText('msg3', 's');

    msg1.target = 'small';
    msg2.target = 'small';
    msg3.target = 'small';

    await bus.publish(msg1);
    await bus.publish(msg2);
    await bus.publish(msg3);

    // msg1 应该被丢弃（最旧的）
    const received1 = await bus.receive('small', 500);
    const received2 = await bus.receive('small', 500);

    // 收到的应该是 msg2 和 msg3
    const contents = [received1?.payload['message'], received2?.payload['message']];
    expect(contents).toContain('msg2');
    expect(contents).toContain('msg3');
    expect(contents).not.toContain('msg1');

    await bus.stop();
  });
});
