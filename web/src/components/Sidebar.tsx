import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Sparkles,
  Layers,
  FlaskConical,
  ScrollText,
  Scale,
  Settings as SettingsIcon,
} from 'lucide-react';
import { api } from '../lib/api';

export type NavTab =
  | 'telemetry-advisor'
  | 'catalog'
  | 'experiments'
  | 'audit'
  | 'evaluation'
  | 'settings';

interface SidebarProps {
  currentTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ currentTab, onSelectTab }) => {
  const { data: pendingData } = useQuery({
    queryKey: ['pending'],
    queryFn: api.getPending,
    refetchInterval: 3000,
  });

  const pendingCount = pendingData?.count || 0;

  const navItemClass = (tab: NavTab) =>
    `flex items-center justify-between px-3 py-2 rounded-md text-xs font-medium font-mono transition-colors ${
      currentTab === tab
        ? 'bg-cyan-950/70 text-cyan-300 border border-cyan-800/80'
        : 'text-zinc-400 hover:bg-industrial-850 hover:text-zinc-200 border border-transparent'
    }`;

  return (
    <aside className="w-64 border-r border-industrial-800 bg-industrial-900/60 p-3 flex flex-col justify-between shrink-0">
      <div className="space-y-6">
        {/* OPERATIONS GROUP */}
        <div>
          <div className="px-3 mb-2 text-[10px] font-mono uppercase tracking-wider text-zinc-500 font-semibold">
            Operations
          </div>
          <nav className="space-y-1">
            <button
              onClick={() => onSelectTab('telemetry-advisor')}
              className={navItemClass('telemetry-advisor')}
            >
              <div className="flex items-center gap-2.5">
                <Sparkles className="w-4 h-4 text-cyan-400" />
                <span>Telemetry & Advisor</span>
              </div>
              {pendingCount > 0 && (
                <span className="px-1.5 py-0.2 rounded-full text-[10px] font-bold bg-amber-500 text-industrial-950">
                  {pendingCount}
                </span>
              )}
            </button>

            <button
              onClick={() => onSelectTab('catalog')}
              className={navItemClass('catalog')}
            >
              <div className="flex items-center gap-2.5">
                <Layers className="w-4 h-4 text-purple-400" />
                <span>Scenario Catalog</span>
              </div>
            </button>

            <button
              onClick={() => onSelectTab('experiments')}
              className={navItemClass('experiments')}
            >
              <div className="flex items-center gap-2.5">
                <FlaskConical className="w-4 h-4 text-rose-400" />
                <span>Experiments Console</span>
              </div>
            </button>
          </nav>
        </div>

        {/* OBSERVATION GROUP */}
        <div>
          <div className="px-3 mb-2 text-[10px] font-mono uppercase tracking-wider text-zinc-500 font-semibold">
            Observation
          </div>
          <nav className="space-y-1">
            <button
              onClick={() => onSelectTab('audit')}
              className={navItemClass('audit')}
            >
              <div className="flex items-center gap-2.5">
                <ScrollText className="w-4 h-4 text-emerald-400" />
                <span>Audit Trail</span>
              </div>
            </button>

            <button
              onClick={() => onSelectTab('evaluation')}
              className={navItemClass('evaluation')}
            >
              <div className="flex items-center gap-2.5">
                <Scale className="w-4 h-4 text-blue-400" />
                <span>Evaluation & Alignment</span>
              </div>
            </button>

            <button
              onClick={() => onSelectTab('settings')}
              className={navItemClass('settings')}
            >
              <div className="flex items-center gap-2.5">
                <SettingsIcon className="w-4 h-4 text-zinc-400" />
                <span>Settings</span>
              </div>
            </button>
          </nav>
        </div>
      </div>

      {/* Footer Info */}
      <div className="p-3 rounded bg-industrial-950 border border-industrial-800 text-[11px] font-mono text-zinc-500">
        <div className="flex justify-between items-center mb-1">
          <span className="text-zinc-400">Environment</span>
          <span className="text-cyan-400 font-semibold">FastAPI SPA</span>
        </div>
        <div className="flex justify-between items-center text-[10px] gap-1">
          <span className="whitespace-nowrap">Security</span>
          <span className="text-emerald-400 font-medium truncate" title="Secrets masked at rest">Secrets masked at rest</span>
        </div>
      </div>
    </aside>
  );
};
