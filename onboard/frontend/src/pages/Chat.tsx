import type { AppName } from '../api/types';
import { ChatPanel } from '../components/ChatPanel';

export function Chat({ appName }: { appName: AppName }) {
  return <ChatPanel appName={appName} />;
}
