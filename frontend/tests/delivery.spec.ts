import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const TASK_ID = 1

test('分段交付：确认、中断、更正、恢复、下载与报表复核', async ({ page, request }) => {
  test.setTimeout(300_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`) })

  // 用接口把来源成员复位，保证可重复执行
  await page.goto('/data_extract')
  await page.waitForTimeout(1500)
  await request.patch('/api/data_extract/1/source-users/U20005', { data: { ltv: 980 } })

  await page.getByTestId(`delivery-${TASK_ID}`).click()
  await expect(page.getByTestId('delivery-panel')).toBeVisible()
  await page.getByTestId('segment-size').fill('2')
  await page.getByTestId('start-delivery').click()
  await expect(page.getByTestId('segment-table')).toBeVisible()
  await expect(page.getByTestId('business-cutoff')).not.toBeEmpty()

  // 确认第一段
  await page.getByTestId('confirm-1').click()
  await expect(page.getByTestId('segment-1')).toHaveAttribute('data-status', '已确认')

  // 中断交付：第二段未送达
  await page.getByTestId('interrupt-delivery').click()
  await expect(page.getByTestId('segment-2')).toHaveAttribute('data-status', '未送达')
  await expect(page.getByTestId('confirmed-count')).toHaveText('2')
  await expect(page.getByTestId('undelivered-count')).toHaveText('1')

  // 部分下载只含第一段，文件里唯一编号
  const partial = page.waitForEvent('download')
  await page.getByTestId('download-delivery').click()
  const partialDownload = await partial
  const partialPath = await partialDownload.path()
  const partialRows = (await readFile(partialPath!, 'utf-8')).trim().split('\n')
  expect(partialRows[0]).toContain('user_id')
  expect(partialRows.length).toBe(3)

  // 运营更正一位成员消费额，跨过门槛
  await request.patch('/api/data_extract/1/source-users/U20005', { data: { ltv: 1600 } })

  // 报表仍按快照：2 人，不含更正成员
  await expect(page.getByTestId('delivery-report')).toBeVisible()
  await expect(page.getByTestId('delivery-report')).toContainText('不并入报表')

  // 恢复交付并确认第二段
  await page.getByTestId('resume-delivery').click()
  await page.getByTestId('confirm-2').click()
  await expect(page.getByTestId('segment-2')).toHaveAttribute('data-status', '已确认')
  await expect(page.getByTestId('confirmed-count')).toHaveText('4')

  // 完整下载：4 个不重复用户，阈值与区域可复核
  await expect(page.getByTestId('file-check')).toBeVisible()
  const full = page.waitForEvent('download')
  await page.getByTestId('download-delivery').click()
  const fullDownload = await full
  const fullPath = await fullDownload.path()
  const lines = (await readFile(fullPath!, 'utf-8')).replace(/^﻿/, '').trim().split('\n')
  const header = lines[0].split(',')
  const body = lines.slice(1).map(line => {
    const cells = line.split(',')
    return Object.fromEntries(header.map((key, i) => [key, cells[i]]))
  })
  expect(body.length).toBe(4)
  expect(new Set(body.map(row => row.user_id)).size).toBe(4)
  for (const row of body) {
    expect(Number(row.ltv)).toBeGreaterThanOrEqual(1000)
    expect(row.region).toBe('华东')
    expect(row.user_id).not.toBe('U20005')
  }

  // 人群报表复用这次交付结果
  await page.getByTestId('publish-report').click()
  await expect(page.getByTestId('delivery-message')).toContainText('人群报表')

  expect(browserErrors).toEqual([])
})
