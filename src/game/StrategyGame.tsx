import { useEffect, useRef } from 'react';
import type { Site } from './StrategyScene';

export default function StrategyGame({ onSelect }: { onSelect: (site: Site) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const onSelectRef = useRef(onSelect);
  useEffect(() => { onSelectRef.current = onSelect; }, [onSelect]);
  useEffect(() => {
    let game: import('phaser').Game | undefined;
    let cancelled = false;
    void (async () => {
      const Phaser = (await import('phaser')).default;
      const { StrategyScene } = await import('./StrategyScene');
      if (cancelled || !host.current) return;
      game = new Phaser.Game({
        type: Phaser.AUTO,
        parent: host.current,
        width: 1120, height: 660,
        backgroundColor: '#07151b',
        scale: { mode: Phaser.Scale.RESIZE, autoCenter: Phaser.Scale.CENTER_BOTH },
        scene: [new StrategyScene(site => onSelectRef.current(site))],
        render: { antialias: true }
      });
    })();
    return () => { cancelled = true; game?.destroy(true); };
  }, []);
  return <div ref={host} className="strategy-canvas" aria-label="Interactive strategy scene with selectable factory, logistics and technology sites" />;
}
