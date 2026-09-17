export interface ExportStep {
  num: string;
  title: string;
  desc: string;
}

export const EXPORT_STEPS: ExportStep[] = [
  {
    num: '01',
    title: 'OPEN INSTAGRAM',
    desc: 'Settings → Accounts Center → Your information and permissions',
  },
  {
    num: '02',
    title: 'REQUEST YOUR DATA',
    desc: 'Tap "Export your information" → "Create export" → choose your account',
  },
  {
    num: '03',
    title: 'PICK YOUR SAVED CONTENT',
    desc: 'Under "customize information", select ONLY "Saved" and "Collections", format HTML, all time',
  },
  {
    num: '04',
    title: 'WAIT FOR THE EMAIL',
    desc: 'Meta will email you a ZIP download link within a few hours',
  },
];
