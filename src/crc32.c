/* ************************************************************************** */
/*                                                                            */
/*                                                        :::      ::::::::   */
/*   crc32.c                                            :+:      :+:    :+:   */
/*                                                    +:+ +:+         +:+     */
/*   By: dlandi <dlandi@student.42sp.org.br>        +#+  +:+       +#+        */
/*                                                +#+#+#+#+#+   +#+           */
/*   Created: 2026/11/16 20:24:00 by dlandi            #+#    #+#             */
/*   Updated: 2026/11/16 20:24:00 by dlandi           ###   ########.fr       */
/*                                                                            */
/* ************************************************************************** */

#include "ledger_int.h"

/*
** CRC-32 as used by zlib, PNG and Ethernet (reflected polynomial
** 0xEDB88320). The Python tests check records with zlib.crc32, so the
** two implementations verify each other.
*/
void	lg_crc_init(uint32_t *tab)
{
	uint32_t	i;
	uint32_t	c;
	int			k;

	i = 0;
	while (i < 256)
	{
		c = i;
		k = 0;
		while (k < 8)
		{
			if (c & 1)
				c = 0xEDB88320u ^ (c >> 1);
			else
				c = c >> 1;
			k++;
		}
		tab[i] = c;
		i++;
	}
}

/*
** Pass 0 as crc to start; pass a previous result to continue.
*/
uint32_t	lg_crc32(const uint32_t *tab, uint32_t crc, const uint8_t *p,
			size_t n)
{
	crc = ~crc;
	while (n > 0)
	{
		crc = tab[(crc ^ *p) & 0xFF] ^ (crc >> 8);
		p++;
		n--;
	}
	return (~crc);
}
