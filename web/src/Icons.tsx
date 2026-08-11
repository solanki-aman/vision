/**
 * The house icon set — professional line icons from lucide-react (the MIT-licensed
 * successor to Feather), re-exported under our own names so the rest of the app never
 * imports lucide directly. One indirection point means we can restyle or swap the set
 * without touching a single call site, and every icon inherits `currentColor` and a
 * consistent 1.75 stroke.
 */
import type { ComponentType } from "react";
import {
  House,
  Inbox,
  LayoutGrid,
  FileText,
  Database,
  ListTree,
  Clock,
  Share2,
  Activity,
  Sunrise,
  TrendingUp,
  Gauge,
  Wallet,
  Target,
  Globe,
  FileSignature,
  Sparkles,
  Pin,
  X,
  RefreshCw,
  ArrowUpRight as ArrowUpRightLucide,
  Check,
  PenLine,
  Sun,
  Moon,
  ChevronRight,
  Settings,
  ArrowUp,
  Plus,
  Search,
  Bell,
  TrendingDown,
  type LucideProps,
} from "lucide-react";

type P = { size?: number } & LucideProps;

// lucide defaults to 24px / stroke 2; we want a slightly finer, calmer line.
const wrap =
  (Comp: ComponentType<LucideProps>) =>
  ({ size = 18, ...rest }: P) =>
    <Comp size={size} strokeWidth={1.75} absoluteStrokeWidth {...rest} />;

// ---- left rail: places you go ----------------------------------------------------
export const HomeIcon = wrap(House);
export const InboxIcon = wrap(Inbox);
export const CanvasIcon = wrap(LayoutGrid);
export const DocumentIcon = wrap(FileText);
export const SourceIcon = wrap(Database);
export const FactsIcon = wrap(ListTree);
export const ScheduleIcon = wrap(Clock);
export const SharedIcon = wrap(Share2);
export const ActivityIcon = wrap(Activity);

// ---- dock: sections of Home ------------------------------------------------------
export const BriefIcon = wrap(Sunrise);
export const RevenueIcon = wrap(TrendingUp);
export const MarginIcon = wrap(Gauge);
export const CashIcon = wrap(Wallet);
export const PlanIcon = wrap(Target);
export const MarketIcon = wrap(Globe);
export const FilingsIcon = wrap(FileSignature);
export const SignalIcon = wrap(Sparkles);

// ---- affordances -----------------------------------------------------------------
export const PinIcon = wrap(Pin);
export const CloseIcon = wrap(X);
export const RefreshIcon = wrap(RefreshCw);
export const ArrowUpRight = wrap(ArrowUpRightLucide);
export const CheckIcon = wrap(Check);
export const EditIcon = wrap(PenLine);
export const SunIcon = wrap(Sun);
export const MoonIcon = wrap(Moon);
export const ChevronIcon = wrap(ChevronRight);
export const SettingsIcon = wrap(Settings);
export const SendIcon = wrap(ArrowUp);
export const PlusIcon = wrap(Plus);
export const SearchIcon = wrap(Search);
export const BellIcon = wrap(Bell);
export const TrendingDownIcon = wrap(TrendingDown);

// section key → icon, so Home renders whatever sections the user has kept
export const SECTION_ICONS: Record<string, (p: P) => JSX.Element> = {
  brief: BriefIcon,
  revenue: RevenueIcon,
  margin: MarginIcon,
  cash: CashIcon,
  plan: PlanIcon,
  market: MarketIcon,
  filings: FilingsIcon,
  signals: SignalIcon,
};
