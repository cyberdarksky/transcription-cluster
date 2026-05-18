import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { api } from '@/lib/api';
import type { InputDirectory, SystemSettings } from '@/types';

function SettingRow({
  label, description, children,
}: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-6 py-4 border-b border-zinc-800/60 last:border-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-zinc-300">{label}</p>
        {description && <p className="text-xs text-zinc-600 mt-0.5">{description}</p>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  );
}

function NumInput({
  value, onChange, min, max,
}: { value: number; onChange: (v: number) => void; min?: number; max?: number }) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      onChange={e => onChange(Number(e.target.value))}
      className="w-24 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-300 font-mono focus:outline-none focus:border-zinc-600 text-right"
    />
  );
}

function DirRow({ dir, onDelete }: { dir: InputDirectory; onDelete: (id: string) => void }) {
  return (
    <div className="flex items-center gap-4 py-3 border-b border-zinc-800/60 last:border-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm text-zinc-300 font-mono truncate">{dir.path}</p>
        <p className="text-xs text-zinc-600 font-mono truncate">→ {dir.output_path}</p>
        {dir.label && <p className="text-xs text-zinc-500 mt-0.5">{dir.label}</p>}
      </div>
      <div className="flex items-center gap-2">
        <span className={clsx(
          'badge',
          dir.is_active ? 'bg-emerald-950 text-emerald-400' : 'bg-zinc-800 text-zinc-500',
        )}>
          {dir.is_active ? 'Aktif' : 'Pasif'}
        </span>
        <button
          onClick={() => onDelete(dir.id)}
          className="btn btn-danger text-xs"
        >
          Kaldır
        </button>
      </div>
    </div>
  );
}

export function Settings() {
  const qc = useQueryClient();
  const [showAddDir, setShowAddDir] = useState(false);
  const [newDir, setNewDir] = useState({ path: '', output_path: '', label: '' });
  const [saved, setSaved] = useState(false);

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.system.settings,
  });
  const { data: dirs } = useQuery({
    queryKey: ['input-dirs'],
    queryFn: api.system.inputDirs,
  });

  const [local, setLocal] = useState<Partial<SystemSettings>>({});
  const merged = { ...settings, ...local } as SystemSettings;

  const updateMut = useMutation({
    mutationFn: (data: Partial<SystemSettings>) => api.system.updateSettings(data),
    onSuccess: () => {
      setSaved(true);
      qc.invalidateQueries({ queryKey: ['settings'] });
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const addDirMut = useMutation({
    mutationFn: () => api.system.addInputDir(newDir),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['input-dirs'] });
      setShowAddDir(false);
      setNewDir({ path: '', output_path: '', label: '' });
    },
  });

  const deleteDirMut = useMutation({
    mutationFn: (id: string) => api.system.deleteInputDir(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['input-dirs'] }),
  });

  const scanMut = useMutation({
    mutationFn: () => api.system.scan(),
  });

  function set<K extends keyof SystemSettings>(key: K, value: SystemSettings[K]) {
    setLocal(prev => ({ ...prev, [key]: value }));
  }

  if (!settings) {
    return <div className="text-zinc-600 text-sm py-8 text-center">Yükleniyor…</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-zinc-100">Ayarlar</h1>

      {/* Input directories */}
      <div className="card">
        <div className="card-header flex items-center justify-between">
          <span className="text-sm font-medium text-zinc-300">İzlenen Giriş Dizinleri</span>
          <div className="flex gap-2">
            <button
              onClick={() => scanMut.mutate()}
              disabled={scanMut.isPending}
              className="btn btn-ghost text-xs"
            >
              {scanMut.isPending ? 'Taranıyor…' : '↺ Şimdi Tara'}
            </button>
            <button onClick={() => setShowAddDir(true)} className="btn btn-primary text-xs">
              + Ekle
            </button>
          </div>
        </div>
        <div className="px-4">
          {dirs?.map(d => (
            <DirRow key={d.id} dir={d} onDelete={id => deleteDirMut.mutate(id)} />
          ))}
          {dirs?.length === 0 && (
            <p className="text-sm text-zinc-600 py-6 text-center">
              Henüz izlenen dizin yok.
            </p>
          )}
        </div>

        {showAddDir && (
          <div className="border-t border-zinc-800 px-4 py-4 bg-zinc-950/30 space-y-3">
            <p className="text-sm font-medium text-zinc-300">Yeni Dizin Ekle</p>
            {([
              { key: 'path', placeholder: '/Volumes/Data/input', label: 'Giriş yolu' },
              { key: 'output_path', placeholder: '/Volumes/Data/output', label: 'Çıktı yolu' },
              { key: 'label', placeholder: 'İsteğe bağlı etiket', label: 'Etiket' },
            ] as const).map(({ key, placeholder, label }) => (
              <div key={key}>
                <label className="text-xs text-zinc-500 mb-1 block">{label}</label>
                <input
                  type="text"
                  placeholder={placeholder}
                  value={newDir[key]}
                  onChange={e => setNewDir(p => ({ ...p, [key]: e.target.value }))}
                  className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-300 font-mono placeholder-zinc-700 focus:outline-none focus:border-zinc-600"
                />
              </div>
            ))}
            <div className="flex gap-2 justify-end">
              <button onClick={() => setShowAddDir(false)} className="btn btn-ghost text-xs">İptal</button>
              <button
                onClick={() => addDirMut.mutate()}
                disabled={!newDir.path || !newDir.output_path || addDirMut.isPending}
                className="btn btn-primary text-xs disabled:opacity-50"
              >
                {addDirMut.isPending ? '…' : 'Ekle'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* System settings */}
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-medium text-zinc-300">Sistem Ayarları</span>
        </div>
        <div className="px-5">
          <SettingRow
            label="Kalp Atışı Zaman Aşımı"
            description="Bu süre (sn) sonra kalp atışı gelmezse işçi offline sayılır"
          >
            <NumInput
              value={merged.worker_heartbeat_timeout_seconds ?? 90}
              onChange={v => set('worker_heartbeat_timeout_seconds', v)}
              min={30}
            />
          </SettingRow>
          <SettingRow
            label="Varsayılan Maksimum Yeniden Deneme"
            description="Her iş için maksimum yeniden deneme sayısı"
          >
            <NumInput
              value={merged.max_retries_default ?? 3}
              onChange={v => set('max_retries_default', v)}
              min={0} max={10}
            />
          </SettingRow>
          <SettingRow
            label="İş Zaman Aşımı Katsayısı"
            description="max_süre = ses_süresi × bu katsayı"
          >
            <NumInput
              value={merged.job_timeout_multiplier ?? 5}
              onChange={v => set('job_timeout_multiplier', v)}
              min={2} max={20}
            />
          </SettingRow>
          <SettingRow
            label="Metrik Saklama Süresi"
            description="İşçi metrik geçmişi kaç gün saklanır"
          >
            <NumInput
              value={merged.worker_metrics_retention_days ?? 7}
              onChange={v => set('worker_metrics_retention_days', v)}
              min={1} max={90}
            />
          </SettingRow>
        </div>
        <div className="px-5 py-4 border-t border-zinc-800 flex justify-end">
          <button
            onClick={() => updateMut.mutate(local)}
            disabled={updateMut.isPending || Object.keys(local).length === 0}
            className={clsx(
              'btn text-sm',
              saved ? 'bg-emerald-600 text-white' : 'btn-primary',
              'disabled:opacity-40',
            )}
          >
            {saved ? '✓ Kaydedildi' : updateMut.isPending ? '…' : 'Kaydet'}
          </button>
        </div>
      </div>

      {/* Read-only info */}
      <div className="card px-5 py-4">
        <p className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-3">Whisper Modeli</p>
        <p className="text-sm font-mono text-zinc-300">{settings.whisper_model}</p>
        <p className="text-xs text-zinc-600 mt-1">
          Dil: <span className="text-zinc-400">{settings.whisper_language}</span>
          {' · '}Kelime zaman damgası: <span className="text-zinc-400">
            {settings.whisper_word_timestamps ? 'Açık' : 'Kapalı'}
          </span>
        </p>
      </div>
    </div>
  );
}
