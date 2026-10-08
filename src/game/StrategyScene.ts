import * as Phaser from 'phaser';

export type Site = { id: string; label: string; kind: string; description: string };
const sites: Site[] = [
  { id: 'factory', label: 'MANUFACTURING', kind: 'Industrial capability', description: 'Illustrative manufacturing capability. Explore production, integration and supplier discovery.' },
  { id: 'warehouse', label: 'LOGISTICS', kind: 'Logistics capability', description: 'Illustrative logistics and supply coordination node.' },
  { id: 'terminal', label: 'TECHNOLOGY', kind: 'Technology capability', description: 'Illustrative dual-use technology and integration opportunity.' }
];

export class StrategyScene extends Phaser.Scene {
  private selectSite: (site: Site) => void;
  private dragging = false;
  private last = { x: 0, y: 0 };
  constructor(selectSite: (site: Site) => void) { super('StrategyScene'); this.selectSite = selectSite; }
  create() {
    const world = this.add.container(0, 0);
    const g = this.add.graphics();
    g.fillStyle(0x0a1a1e); g.fillRect(-1600, -1200, 3200, 2400);
    for (let i = -16; i <= 16; i++) {
      g.lineStyle(1, 0x1b4140, 0.5);
      g.lineBetween(i * 90, -1100, i * 90, 1100);
      g.lineBetween(-1500, i * 90, 1500, i * 90);
    }
    g.lineStyle(2, 0x37665d, 0.6);
    g.strokeRect(-720, -470, 1440, 940);
    world.add(g);
    const labels = this.add.container(0, 0);
    world.add(labels);
    const places = [
      { site: sites[0], x: -310, y: -100, w: 260, h: 150, color: 0x397b6c },
      { site: sites[1], x: 150, y: 140, w: 220, h: 130, color: 0x977e50 },
      { site: sites[2], x: 330, y: -230, w: 180, h: 140, color: 0x577ea0 }
    ];
    for (const p of places) {
      const footprint = this.add.graphics();
      footprint.fillStyle(0x081318, 0.9); footprint.fillRect(p.x + 15, p.y + 20, p.w, p.h);
      footprint.fillStyle(p.color, 0.8); footprint.fillRect(p.x, p.y, p.w, p.h);
      footprint.lineStyle(3, 0xb1e9d4); footprint.strokeRect(p.x, p.y, p.w, p.h);
      footprint.lineStyle(1, 0xb1e9d4, 0.45);
      for (let j = 1; j < 4; j++) footprint.lineBetween(p.x + j * p.w / 4, p.y, p.x + j * p.w / 4, p.y + p.h);
      const zone = this.add.zone(p.x + p.w / 2, p.y + p.h / 2, p.w, p.h).setInteractive({ useHandCursor: true });
      zone.on('pointerdown', (_pointer: Phaser.Input.Pointer, _localX: number, _localY: number, event: Phaser.Types.Input.EventData) => { event.stopPropagation(); this.selectSite(p.site); });
      labels.add([footprint, zone, this.add.text(p.x, p.y - 28, p.site.label, { fontFamily: 'monospace', fontSize: '18px', color: '#d6f4e9', backgroundColor: '#0a1a1e', padding: { x: 7, y: 5 } })]);
    }
    const route = this.add.graphics();
    route.lineStyle(4, 0x76d4b4, 0.75); route.lineBetween(-175, 0, 150, 210); route.lineBetween(360, 170, 420, -90);
    world.add(route);
    const marker = this.add.triangle(0, 0, 0, -20, -15, 16, 15, 16, 0xd9b376);
    this.tweens.add({ targets: marker, x: 100, y: 110, duration: 4200, yoyo: true, repeat: -1 });
    world.add(marker);
    this.cameras.main.centerOn(0, 0);
    this.input.on('pointerdown', (p: Phaser.Input.Pointer) => { this.dragging = true; this.last = { x: p.x, y: p.y }; });
    this.input.on('pointerup', () => { this.dragging = false; });
    this.input.on('pointermove', (p: Phaser.Input.Pointer) => {
      if (!this.dragging) return;
      const cam = this.cameras.main;
      cam.scrollX -= (p.x - this.last.x) / cam.zoom;
      cam.scrollY -= (p.y - this.last.y) / cam.zoom;
      this.last = { x: p.x, y: p.y };
    });
    this.input.on('wheel', (_p: Phaser.Input.Pointer, _o: Phaser.GameObjects.GameObject[], _dx: number, dy: number) => {
      const cam = this.cameras.main; cam.setZoom(Phaser.Math.Clamp(cam.zoom - dy * 0.001, 0.55, 2));
    });
    this.add.text(22, 22, 'FIELD SECTOR / UKRAINE     ·     SYNTHETIC DEMONSTRATION', { fontFamily: 'monospace', fontSize: '13px', color: '#a3d6c7', backgroundColor: '#09191c', padding: { x: 10, y: 8 } }).setScrollFactor(0);
  }
}
