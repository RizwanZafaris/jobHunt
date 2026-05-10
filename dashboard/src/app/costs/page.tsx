/**
 * /costs → /insights?tab=costs (back-compat).
 */
import { redirect } from 'next/navigation'

export default function CostsRedirect(): never {
  redirect('/insights?tab=costs')
}
