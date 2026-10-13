/** Trang Quản lý người dùng: tạo, cập nhật vai trò, kích hoạt và đặt lại mật khẩu. Chỉ ADMIN. */

import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Modal,
  Select,
  Spinner,
  Table,
  type Column,
} from "@/components/ui";
import {
  useCreateUser,
  useRoles,
  useUpdateUser,
  useUsers,
} from "@/hooks/queries";
import { describeError } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";
import { useAuth } from "@/store/auth";
import type { Role, User, UserCreate, UserUpdate } from "@/types";

const ROLE_LABELS: Record<Role, string> = {
  VIEWER: "Xem (Viewer)",
  AGENT: "Tác nhân (Agent)",
  OPERATOR: "Vận hành (Operator)",
  ADMIN: "Quản trị (Admin)",
};

function roleLabel(role: Role): string {
  return ROLE_LABELS[role] ?? role;
}

const DEFAULT_CREATE: UserCreate = {
  email: "",
  full_name: "",
  role: "VIEWER",
  password: "",
};

export default function UsersPage() {
  const { user: me } = useAuth();
  const { data, isLoading, isError, error } = useUsers();
  const { data: roles } = useRoles();

  const [createOpen, setCreateOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);

  const create = useCreateUser();
  const update = useUpdateUser();

  const users = data ?? [];

  const columns: Column<User>[] = [
    {
      key: "full_name",
      header: "Họ tên",
      render: (u) => (
        <div>
          <p className="font-medium text-slate-800">{u.full_name || "—"}</p>
          <p className="text-xs text-slate-500">{u.email}</p>
        </div>
      ),
    },
    {
      key: "role",
      header: "Vai trò",
      render: (u) => <Badge tone="info">{roleLabel(u.role)}</Badge>,
    },
    {
      key: "is_active",
      header: "Trạng thái",
      render: (u) =>
        u.is_active ? (
          <Badge tone="success">Đang hoạt động</Badge>
        ) : (
          <Badge tone="danger">Đã khóa</Badge>
        ),
    },
    {
      key: "last_login_at",
      header: "Đăng nhập gần nhất",
      render: (u) => (
        <span className="text-slate-500">{u.last_login_at ? formatDateTime(u.last_login_at) : "—"}</span>
      ),
    },
    {
      key: "actions",
      header: "",
      render: (u) => (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setEditId(u.id)} disabled={u.id === me?.id}>
            Sửa
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">Quản lý người dùng</h1>
          <p className="text-sm text-slate-500">
            Tạo tài khoản, phân quyền và quản lý trạng thái truy cập hệ thống.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>Thêm người dùng</Button>
      </div>

      <Card>
        {isLoading ? (
          <div className="flex items-center justify-center py-10">
            <Spinner />
          </div>
        ) : isError ? (
          <p className="py-6 text-center text-sm text-rose-600">{describeError(error).message}</p>
        ) : users.length === 0 ? (
          <EmptyState title="Chưa có người dùng nào" />
        ) : (
          <Table
            columns={columns}
            rows={users}
            keyOf={(u) => u.id}
            empty={<EmptyState title="Chưa có người dùng nào" />}
          />
        )}
      </Card>

      {createOpen && (
        <CreateUserModal
          roles={roles ?? []}
          onClose={() => setCreateOpen(false)}
          onSubmit={async (req) => {
            await create.mutateAsync(req);
            setCreateOpen(false);
          }}
          error={create.isError ? describeError(create.error).message : null}
          pending={create.isPending}
        />
      )}

      {editId && (
        <EditUserModal
          user={users.find((u) => u.id === editId) ?? null}
          roles={roles ?? []}
          onClose={() => setEditId(null)}
          onSubmit={async (payload) => {
            await update.mutateAsync({ id: editId, payload });
            setEditId(null);
          }}
          error={update.isError ? describeError(update.error).message : null}
          pending={update.isPending}
        />
      )}
    </div>
  );
}

function CreateUserModal({
  roles,
  onSubmit,
  onClose,
  error,
  pending,
}: {
  roles: Role[];
  onSubmit: (req: UserCreate) => Promise<void>;
  onClose: () => void;
  error: string | null;
  pending: boolean;
}) {
  const [form, setForm] = useState<UserCreate>(DEFAULT_CREATE);
  const [touched, setTouched] = useState(false);

  function set<K extends keyof UserCreate>(key: K, value: UserCreate[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  const passwordError = touched
    ? validatePassword(form.password)
    : null;
  const emailError =
    touched && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email)
      ? "Email không hợp lệ."
      : null;
  const nameError = touched && form.full_name.trim().length === 0 ? "Vui lòng nhập họ tên." : null;
  const canSubmit = !passwordError && !emailError && !nameError;

  return (
    <Modal
      open
      onClose={onClose}
      title="Thêm người dùng"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Hủy
          </Button>
          <Button
            onClick={() => {
              setTouched(true);
              if (canSubmit) void onSubmit(form);
            }}
            disabled={pending || !canSubmit}
          >
            {pending ? "Đang tạo…" : "Tạo người dùng"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Họ tên">
          <input
            className="input"
            value={form.full_name}
            onChange={(e) => set("full_name", e.target.value)}
            placeholder="Nguyễn Văn A"
          />
          {nameError && <span className="mt-1 block text-xs text-rose-600">{nameError}</span>}
        </Field>
        <Field label="Email">
          <input
            className="input"
            type="email"
            value={form.email}
            onChange={(e) => set("email", e.target.value.trim())}
            placeholder="ten@airdesk.vn"
          />
          {emailError && <span className="mt-1 block text-xs text-rose-600">{emailError}</span>}
        </Field>
        <Field label="Vai trò">
          <Select value={form.role} onChange={(e) => set("role", e.target.value as Role)}>
            {(roles.length ? roles : (["VIEWER", "AGENT", "OPERATOR", "ADMIN"] as Role[])).map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Mật khẩu" hint="Ít nhất 8 ký tự, gồm cả chữ và số.">
          <input
            className="input"
            type="password"
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
            placeholder="••••••••"
          />
          {passwordError && (
            <span className="mt-1 block text-xs text-rose-600">{passwordError}</span>
          )}
        </Field>
        {error && <p className="text-sm text-rose-600">{error}</p>}
      </div>
    </Modal>
  );
}

function EditUserModal({
  user,
  roles,
  onSubmit,
  onClose,
  error,
  pending,
}: {
  user: User | null;
  roles: Role[];
  onSubmit: (payload: UserUpdate) => Promise<void>;
  onClose: () => void;
  error: string | null;
  pending: boolean;
}) {
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [role, setRole] = useState<Role>(user?.role ?? "VIEWER");
  const [isActive, setIsActive] = useState<boolean>(user?.is_active ?? true);
  const [password, setPassword] = useState("");
  const [touchedPw, setTouchedPw] = useState(false);

  const passwordError = touchedPw && password.length > 0 ? validatePassword(password) : null;

  return (
    <Modal
      open
      onClose={onClose}
      title="Chỉnh sửa người dùng"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Hủy
          </Button>
          <Button
            onClick={() => {
              if (password.length > 0) setTouchedPw(true);
              if (password.length > 0 && passwordError) return;
              const payload: UserUpdate = {
                full_name: fullName,
                role,
                is_active: isActive,
              };
              if (password.length > 0) payload.password = password;
              void onSubmit(payload);
            }}
            disabled={pending}
          >
            {pending ? "Đang lưu…" : "Lưu thay đổi"}
          </Button>
        </>
      }
    >
      {user && (
        <div className="space-y-3">
          <Field label="Email">
            <input className="input" value={user.email} disabled />
          </Field>
          <Field label="Họ tên">
            <input className="input" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </Field>
          <Field label="Vai trò">
            <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {(roles.length ? roles : (["VIEWER", "AGENT", "OPERATOR", "ADMIN"] as Role[])).map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Trạng thái truy cập">
            <Select value={isActive ? "active" : "inactive"} onChange={(e) => setIsActive(e.target.value === "active")}>
              <option value="active">Đang hoạt động</option>
              <option value="inactive">Đã khóa</option>
            </Select>
          </Field>
          <Field label="Đặt lại mật khẩu" hint="Để trống nếu không muốn đổi. Ít nhất 8 ký tự, gồm cả chữ và số.">
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
            {passwordError && (
              <span className="mt-1 block text-xs text-rose-600">{passwordError}</span>
            )}
          </Field>
          {error && <p className="text-sm text-rose-600">{error}</p>}
        </div>
      )}
    </Modal>
  );
}

function validatePassword(value: string): string | null {
  if (value.length < 8) return "Mật khẩu phải có ít nhất 8 ký tự.";
  if (!/[a-zA-Z]/.test(value) || !/[0-9]/.test(value))
    return "Mật khẩu phải chứa cả chữ và số.";
  return null;
}
