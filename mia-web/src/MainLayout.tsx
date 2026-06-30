import { useEffect, useState } from 'react'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu, Select, App as AntApp } from 'antd'
import { MessageOutlined, HistoryOutlined, DatabaseOutlined, SettingOutlined } from '@ant-design/icons'
import { fetchSessions, activateSession, fetchSessionHistory, fetchChannels } from './api'

const { Header, Sider, Content } = Layout

export default function MainLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [sessions, setSessions] = useState<any[]>([])
  const [currentId, setCurrentId] = useState('')
  const [channels, setChannels] = useState<any>({})
  const [messages, setMessages] = useState<any[]>([])

  const selectedKey = '/' + location.pathname.split('/')[1] || '/chat'

  useEffect(() => {
    (async () => {
      const s = await fetchSessions()
      setSessions(s.sessions || [])
      setCurrentId(s.current_id)
      if (s.current_id) {
        const h = await fetchSessionHistory(s.current_id)
        setMessages(h.messages || [])
      }
      const c = await fetchChannels()
      setChannels(c)
    })()
  }, [])

  const switchSession = async (id: string) => {
    await activateSession(id)
    setCurrentId(id)
    const h = await fetchSessionHistory(id)
    setMessages(h.messages || [])
  }

  return (
    <AntApp>
      <Layout style={{ height: '100vh' }}>
        <Header style={{ height: 48, lineHeight: '48px', background: '#001529', display: 'flex', alignItems: 'center', padding: '0 16px', gap: 16 }}>
          <span style={{ color: '#fff', fontWeight: 'bold', fontSize: 16, whiteSpace: 'nowrap' }}>MIA 控制台</span>
          <Select
            value={currentId || undefined}
            onChange={switchSession}
            placeholder="选择会话"
            size="small"
            style={{ minWidth: 200 }}
            options={sessions.map((s: any) => ({ label: `${s.name} (${s.turn_count})`, value: s.session_id }))}
          />
          <div style={{ flex: 1 }} />
          <Menu
            theme="dark"
            mode="horizontal"
            selectedKeys={[selectedKey]}
            onClick={({ key }) => navigate(key)}
            items={[
              { key: '/chat', icon: <MessageOutlined />, label: '聊天' },
              { key: '/sessions', icon: <HistoryOutlined />, label: '会话' },
              { key: '/memory', icon: <DatabaseOutlined />, label: '记忆' },
              { key: '/settings', icon: <SettingOutlined />, label: '设置' },
            ]}
          />
        </Header>
        <Layout style={{ height: 0, flex: 1 }}>
          <Sider width={220} theme="light" style={{ borderRight: '1px solid #f0f0f0', padding: 12, overflow: 'auto' }}>
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>渠道状态</div>
              <div style={{ fontSize: 12 }}>
                WeChat: {channels?.wechat?.enabled ? <span style={{ color: '#52c41a' }}>ON</span> : <span style={{ color: '#999' }}>OFF</span>}
                &nbsp;|&nbsp;
                Telegram: {channels?.telegram?.enabled ? <span style={{ color: '#52c41a' }}>ON</span> : <span style={{ color: '#999' }}>OFF</span>}
              </div>
            </div>
            <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>会话列表</div>
            {sessions.map((s: any) => (
              <div
                key={s.session_id}
                onClick={() => switchSession(s.session_id)}
                style={{
                  padding: '4px 8px', cursor: 'pointer', borderRadius: 4, marginBottom: 2, fontSize: 13,
                  background: s.session_id === currentId ? '#e6f4ff' : 'transparent',
                  color: s.session_id === currentId ? '#1677ff' : '#333',
                }}
              >
                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</div>
                <div style={{ fontSize: 11, color: '#999' }}>{s.source} | {s.turn_count} 轮</div>
              </div>
            ))}
          </Sider>
          <Content style={{ padding: 16, overflow: 'auto' }}>
            <Outlet context={{ sessions, switchSession, channels, messages, setMessages, currentId }} />
          </Content>
        </Layout>
      </Layout>
    </AntApp>
  )
}