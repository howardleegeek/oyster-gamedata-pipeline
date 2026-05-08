import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="max-w-xl mx-auto px-4 py-24 text-center">
      <h1 className="text-6xl font-extrabold text-amber-accent mb-3">404</h1>
      <p className="text-oyster-300 mb-8">
        That page doesn&apos;t exist (or your invite link expired).
      </p>
      <Link href="/" className="btn-primary">
        Back to home
      </Link>
    </div>
  );
}
