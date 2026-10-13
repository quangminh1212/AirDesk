import { Link } from "react-router-dom";

import { Button, Card } from "@/components/ui";

export default function NotFoundPage() {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <Card className="max-w-md text-center">
        <p className="text-4xl font-semibold text-slate-300">404</p>
        <h1 className="mt-2 text-base font-semibold text-slate-700">Không tìm thấy trang</h1>
        <p className="mt-1 text-sm text-slate-500">
          Đường dẫn bạn truy cập không tồn tại hoặc đã bị di chuyển.
        </p>
        <div className="mt-4 flex justify-center">
          <Link to="/">
            <Button>Về trang tổng quan</Button>
          </Link>
        </div>
      </Card>
    </div>
  );
}
