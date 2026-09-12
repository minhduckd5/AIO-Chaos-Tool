import React, { useState } from 'react';
import { TopBar } from './components/TopBar';
import { Sidebar, NavTab } from './components/Sidebar';
import { EventDrawer } from './components/EventDrawer';
import { useEventStream } from './hooks/useEventStream';

import { TelemetryAdvisorPage } from './pages/TelemetryAdvisorPage';
import { CatalogPage } from './pages/CatalogPage';
import { ExperimentsPage } from './pages/ExperimentsPage';
import { AuditPage } from './pages/AuditPage';
import { EvaluationPage } from './pages/EvaluationPage';
import { SettingsPage } from './pages/SettingsPage';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<NavTab>('telemetry-advisor');
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { connected, events, clearEvents } = useEventStream();

  return (
    <div className="flex flex-col h-screen bg-industrial-950 text-zinc-100 font-sans overflow-hidden">
      {/* TopBar with Honesty Pill & Operator Identity */}
      <TopBar
        onToggleDrawer={() => setDrawerOpen(!drawerOpen)}
        streamConnected={connected}
        drawerOpen={drawerOpen}
      />

      {/* Main Workspace Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Grouped Sidebar */}
        <Sidebar currentTab={currentTab} onSelectTab={setCurrentTab} />

        {/* Dynamic Main Workspace Area */}
        <main className="flex-1 overflow-y-auto bg-industrial-950">
          {currentTab === 'telemetry-advisor' && <TelemetryAdvisorPage />}
          {currentTab === 'catalog' && <CatalogPage />}
          {currentTab === 'experiments' && <ExperimentsPage />}
          {currentTab === 'audit' && <AuditPage />}
          {currentTab === 'evaluation' && <EvaluationPage />}
          {currentTab === 'settings' && <SettingsPage />}
        </main>

        {/* Live SSE Slide-over Event Drawer */}
        <EventDrawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          events={events}
          onClear={clearEvents}
          connected={connected}
        />
      </div>
    </div>
  );
};
