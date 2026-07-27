import { NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = (
  process.env.NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL
  || process.env.LEARNHOUSE_BACKEND_URL
  || 'http://localhost:1338'
).replace(/\/+$/, '')
const MEDIA_URL = (
  process.env.NEXT_PUBLIC_LEARNHOUSE_MEDIA_URL
  || BACKEND_URL
).replace(/\/+$/, '')

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ podcastuuid: string }> }
) {
  const { podcastuuid } = await params
  const orgSlug = request.headers.get('X-Feed-Orgslug') || request.nextUrl.searchParams.get('orgslug')

  if (!orgSlug) {
    return NextResponse.json({ error: 'Missing org context' }, { status: 400 })
  }

  try {
    const org = await fetchBackendJson(`/api/v1/orgs/slug/${encodeURIComponent(orgSlug)}`)
    const podcastMeta = await fetchBackendJson(`/api/v1/podcasts/${encodeURIComponent(`podcast_${podcastuuid}`)}/meta`)

    if (!podcastMeta?.podcast) {
      return NextResponse.json({ error: 'Podcast not found' }, { status: 404 })
    }

    const { podcast, episodes } = podcastMeta
    const baseUrl = getRequestOrigin(request)
    const podcastUrl = `${baseUrl}podcast/${podcastuuid}`

    const imageUrl = podcast.thumbnail_image
      ? getPodcastThumbnailMediaDirectory(org.org_uuid, podcast.podcast_uuid, podcast.thumbnail_image)
      : ''

    // Build episodes XML
    const episodeItems = (episodes || [])
      .filter((ep: any) => ep.published)
      .sort((a: any, b: any) => b.episode_number - a.episode_number)
      .map((episode: any) => {
        const audioUrl = episode.audio_file
          ? getEpisodeAudioMediaDirectory(org.org_uuid, podcast.podcast_uuid, episode.episode_uuid, episode.audio_file)
          : ''
        const epImageUrl = episode.thumbnail_image
          ? getEpisodeThumbnailMediaDirectory(org.org_uuid, podcast.podcast_uuid, episode.episode_uuid, episode.thumbnail_image)
          : imageUrl
        const pubDate = episode.creation_date ? new Date(episode.creation_date).toUTCString() : ''
        const durationFormatted = formatDuration(episode.duration_seconds || 0)

        return `    <item>
      <title>${escapeXml(episode.title)}</title>
      <description>${escapeXml(episode.description || '')}</description>
      <pubDate>${pubDate}</pubDate>
      <enclosure url="${escapeXml(audioUrl)}" type="audio/mpeg" />
      <guid isPermaLink="false">${episode.episode_uuid}</guid>
      <itunes:episode>${episode.episode_number}</itunes:episode>
      <itunes:duration>${durationFormatted}</itunes:duration>
      <itunes:summary>${escapeXml(episode.description || '')}</itunes:summary>
      ${epImageUrl ? `<itunes:image href="${escapeXml(epImageUrl)}" />` : ''}
    </item>`
      }).join('\n')

    // Get author name from podcast authors array
    const authorName = podcast.authors?.[0]?.user
      ? `${podcast.authors[0].user.first_name} ${podcast.authors[0].user.last_name}`.trim()
      : org.name

    const rss = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${escapeXml(podcast.name)}</title>
    <link>${escapeXml(podcastUrl)}</link>
    <description>${escapeXml(podcast.description || podcast.about || '')}</description>
    <language>en</language>
    <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>
    <atom:link href="${escapeXml(`${baseUrl}api/podcast/${podcastuuid}/feed`)}" rel="self" type="application/rss+xml" />
    <itunes:author>${escapeXml(authorName)}</itunes:author>
    <itunes:summary>${escapeXml(podcast.description || podcast.about || '')}</itunes:summary>
    ${imageUrl ? `<itunes:image href="${escapeXml(imageUrl)}" />` : ''}
    ${imageUrl ? `<image>
      <url>${escapeXml(imageUrl)}</url>
      <title>${escapeXml(podcast.name)}</title>
      <link>${escapeXml(podcastUrl)}</link>
    </image>` : ''}
    <itunes:owner>
      <itunes:name>${escapeXml(authorName)}</itunes:name>
    </itunes:owner>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
${episodeItems}
  </channel>
</rss>`

    return new NextResponse(rss, {
      headers: {
        'Content-Type': 'application/rss+xml; charset=utf-8',
        'Cache-Control': 'public, max-age=3600, s-maxage=3600',
      },
    })
  } catch (error) {
    return NextResponse.json({ error: 'Failed to generate feed' }, { status: 500 })
  }
}

async function fetchBackendJson(path: string) {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(`Backend request failed: ${response.status}`)
  }
  return response.json()
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function getRequestOrigin(request: NextRequest): string {
  const forwardedProto = request.headers.get('x-forwarded-proto')?.split(',')[0]?.trim()
  const protocol = forwardedProto || request.nextUrl.protocol.replace(/:$/, '') || 'https'
  const host = request.headers.get('x-forwarded-host') || request.headers.get('host') || request.nextUrl.host
  return `${protocol}://${host}/`
}

function getPodcastThumbnailMediaDirectory(
  orgUUID: string,
  podcastUUID: string,
  fileId: string
) {
  return `${MEDIA_URL}/content/orgs/${orgUUID}/podcasts/${podcastUUID}/thumbnails/${fileId}`
}

function getEpisodeThumbnailMediaDirectory(
  orgUUID: string,
  podcastUUID: string,
  episodeUUID: string,
  fileId: string
) {
  return `${MEDIA_URL}/content/orgs/${orgUUID}/podcasts/${podcastUUID}/episodes/${episodeUUID}/thumbnails/${fileId}`
}

function getEpisodeAudioMediaDirectory(
  orgUUID: string,
  podcastUUID: string,
  episodeUUID: string,
  fileId: string
) {
  return `${MEDIA_URL}/content/orgs/${orgUUID}/podcasts/${podcastUUID}/episodes/${episodeUUID}/audio/${fileId}`
}

function formatDuration(seconds: number): string {
  const hrs = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${mins}:${String(secs).padStart(2, '0')}`
}
